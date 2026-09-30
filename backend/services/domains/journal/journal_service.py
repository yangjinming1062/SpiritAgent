"""伙伴时刻 / 日记服务：精灵主导时间线的写入与节流、评论区、夜间批处理投影。``memories`` 表不动；moments / diary 是给用户看的展示面，不是检索向量。片刻完全由精灵发起（夜间规划、聊天工具、白天自主冲动），用户只能评论、隐藏。"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    SESSION_LOCAL,
    SETTINGS,
    ensure_utc,
    get_logger,
    utc_now,
)
from modules.companion import (
    CompanionDiaryEntry,
    CompanionMoment,
    CompanionMomentComment,
    DiaryEntryResponse,
    DiarySource,
    MomentCommentResponse,
    MomentCommentRole,
    MomentResponse,
    MomentSource,
)
from modules.conversation import Message
from modules.ws import emit_ws_event
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import get_or_create_special_conversation
from services.domains.memory import resolve_user_timezone
from services.infrastructure.assets import signed_companion_asset_url

logger = get_logger(__name__)

# 当日发布的片刻只把最近这些交给夜间模型阅读；日记仍关联当日全部片刻。
_INTERACTION_POSTED_LIMIT = 30


class JournalError(RuntimeError):
    pass


class MomentNotFoundError(JournalError):
    pass


@dataclass(frozen=True)
class MomentInteractions:
    """本地日片刻互动：threads 为交给夜间模型阅读的片刻与评论线程，posted_ids 为当日发布的全部片刻。"""

    threads: list[dict[str, Any]]
    posted_ids: list[str]


def _require_asset_path(path: str | None) -> str | None:
    """片刻只引用正式资产（``companion-assets/{user_id}/``），展示与备份不留过期的外部或临时地址。"""
    if path is not None and not path.startswith("companion-assets/"):
        raise JournalError("moment media must be a persisted companion asset")
    return path


def _client_url(path: str | None) -> str | None:
    return (signed_companion_asset_url(path) or path) if path else path


def response_for_comment(row: CompanionMomentComment) -> MomentCommentResponse:
    return MomentCommentResponse.model_validate(row, from_attributes=True)


def response_for_moment(row: CompanionMoment) -> MomentResponse:
    return MomentResponse.model_validate(row, from_attributes=True).model_copy(
        update={"media_url": _client_url(row.media_url), "audio_url": _client_url(row.audio_url)},
    )


async def list_moments(
    db: AsyncSession,
    user_id: int,
    *,
    cursor: str | None = None,
    limit: int = 20,
    kind: str | None = None,
) -> tuple[list[CompanionMoment], str | None]:
    limit = max(1, min(100, int(limit)))
    stmt = (
        select(CompanionMoment)
        .where(CompanionMoment.user_id == user_id)
        .order_by(CompanionMoment.occurred_at.desc(), CompanionMoment.id.desc())
    )
    if kind:
        stmt = stmt.where(CompanionMoment.kind == kind)
    if cursor:
        try:
            cursor_dt = ensure_utc(datetime.fromisoformat(cursor))
        except (ValueError, TypeError):
            cursor_dt = None
        if cursor_dt is not None:
            stmt = stmt.where(CompanionMoment.occurred_at < cursor_dt)
    rows = (await db.execute(stmt.limit(limit + 1))).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = rows[-1].occurred_at.isoformat()
    return list(rows), next_cursor


async def create_user_moment(
    db: AsyncSession,
    user_id: int,
    *,
    title: str,
    body: str,
    kind: str,
    source: str,
    emotion: str | None = None,
    media_url: str | None = None,
    media_type: str = "",
    audio_url: str | None = None,
    media_metadata: dict[str, Any] | None = None,
    session_id: int | None = None,
) -> CompanionMoment:
    if not title.strip() or len(title.strip()) > 64 or len(body.strip()) > 500:
        raise ValueError("片刻标题须为 1–64 字符，正文最多 500 字符；内容尚未保存")
    media_url = _require_asset_path(media_url)
    audio_url = _require_asset_path(audio_url)
    row = CompanionMoment(
        id=str(uuid4()),
        user_id=user_id,
        kind=kind,
        title=title.strip(),
        body=body.strip(),
        emotion=(emotion or None),
        media_url=media_url,
        media_type=media_type,
        audio_url=audio_url,
        media_metadata=media_metadata,
        source=source,
        session_id=session_id,
    )
    if source == MomentSource.NIGHTLY.value:
        conversation = await get_or_create_special_conversation(db, user_id, "companion")
        row.session_id = conversation.id
        media = [{"type": media_type, "url": media_url}] if media_url else []
        if audio_url and media:
            media[0]["audio_url"] = audio_url
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="\n\n".join(part for part in (row.title, row.body) if part),
            subtype="status_media" if media else "status_proactive",
            media_json=json.dumps(media, ensure_ascii=False) if media else None,
        )
        db.add(message)
        await db.flush()
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.message",
            payload={
                "text": message.content,
                "session_id": str(conversation.id),
                "message_id": message.id,
                "media": [
                    {key: _client_url(value) if key != "type" else value for key, value in item.items()}
                    for item in media
                ],
            },
        )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    # 新建行未经查询，refresh 不填充 selectin 关系；显式置空避免事件序列化触发异步惰性加载。
    row.comments = []
    await _emit_event(user_id, "companion.moment.created", response_for_moment(row).model_dump())
    return row


async def _count_recent_source_moments(db: AsyncSession, user_id: int, source: str) -> int:
    since = utc_now() - timedelta(hours=24)
    return (
        await db.execute(
            select(func.count(CompanionMoment.id)).where(
                CompanionMoment.user_id == user_id,
                CompanionMoment.source == source,
                CompanionMoment.created_at >= since,
            ),
        )
    ).scalar_one()


async def check_moment_llm_quota(db: AsyncSession, user_id: int) -> bool:
    """聊天内 moment_create 每日配额：每用户每 24h ≤ moment_llm_per_day（默认 3，0 表示关闭该通道）。"""
    return await _count_recent_source_moments(
        db,
        user_id,
        MomentSource.LLM.value,
    ) < int(SETTINGS.moment_llm_per_day)


async def check_moment_autonomous_quota(db: AsyncSession, user_id: int) -> bool:
    """白天自主冲动发布每日配额：每用户每 24h ≤ moment_autonomous_per_day（默认 3，0 表示关闭该通道）。"""
    return await _count_recent_source_moments(
        db,
        user_id,
        MomentSource.AUTONOMOUS.value,
    ) < int(SETTINGS.moment_autonomous_per_day)


async def get_moment(db: AsyncSession, user_id: int, moment_id: str) -> CompanionMoment | None:
    return (
        await db.execute(
            select(CompanionMoment).where(
                CompanionMoment.id == moment_id,
                CompanionMoment.user_id == user_id,
            ),
        )
    ).scalar_one_or_none()


async def create_moment_comment(
    db: AsyncSession,
    user_id: int,
    moment_id: str,
    *,
    content: str,
    role: str = MomentCommentRole.USER.value,
) -> CompanionMomentComment:
    if not content.strip() or len(content.strip()) > 500:
        raise ValueError("评论须为 1–500 字符；内容尚未保存")
    moment = await get_moment(db, user_id, moment_id)
    if moment is None:
        raise MomentNotFoundError(f"moment {moment_id} not found")
    row = CompanionMomentComment(
        id=str(uuid4()),
        moment_id=moment.id,
        user_id=user_id,
        role=role,
        content=content.strip(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _emit_event(
        user_id,
        "companion.moment.comment",
        {"moment_id": row.moment_id, "comment": response_for_comment(row).model_dump()},
    )
    return row


async def delete_moment_comment(db: AsyncSession, user_id: int, moment_id: str, comment_id: str) -> None:
    """仅允许删除本人的 user 评论；companion 评论只能随整条片刻隐藏。"""
    row = (
        await db.execute(
            select(CompanionMomentComment).where(
                CompanionMomentComment.id == comment_id,
                CompanionMomentComment.moment_id == moment_id,
                CompanionMomentComment.user_id == user_id,
                CompanionMomentComment.role == MomentCommentRole.USER.value,
            ),
        )
    ).scalar_one_or_none()
    if row is None:
        raise MomentNotFoundError(f"comment {comment_id} not found")
    await db.delete(row)
    await db.commit()


def response_for_diary(row: CompanionDiaryEntry) -> DiaryEntryResponse:
    return DiaryEntryResponse.model_validate(row, from_attributes=True)


async def list_diary(
    db: AsyncSession,
    user_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 100,
) -> list[CompanionDiaryEntry]:
    limit = max(1, min(365, int(limit)))
    stmt = select(CompanionDiaryEntry).where(CompanionDiaryEntry.user_id == user_id)
    if date_from is not None:
        stmt = stmt.where(CompanionDiaryEntry.entry_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(CompanionDiaryEntry.entry_date <= date_to)
    stmt = stmt.order_by(CompanionDiaryEntry.entry_date.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_diary_by_date(
    db: AsyncSession,
    user_id: int,
    entry_date: date,
) -> CompanionDiaryEntry | None:
    return (
        await db.execute(
            select(CompanionDiaryEntry).where(
                CompanionDiaryEntry.user_id == user_id,
                CompanionDiaryEntry.entry_date == entry_date,
            ),
        )
    ).scalar_one_or_none()


async def upsert_diary(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: date,
    title: str,
    body: str,
    mood: str | None = None,
    source: str,
    moment_ids: list[str] | None = None,
    _retried: bool = False,
) -> CompanionDiaryEntry:
    title, body = title.strip(), body.strip()
    if len(title) > 128 or not body or len(body) > 2000:
        raise ValueError("日记标题最多 128 字符，本次正文须为 1–2000 字符；内容尚未保存")
    row = await get_diary_by_date(db, user_id, entry_date)
    if row is None:
        row = CompanionDiaryEntry(
            id=str(uuid4()),
            user_id=user_id,
            entry_date=entry_date,
            title=title,
            body=body,
            mood=mood,
            source=source,
            moment_ids=moment_ids or [],
        )
        db.add(row)
    else:
        # 同日已有日记时只追加不覆盖：夜间补记带专属分隔语，LLM 补记合并正文与标题。
        sep = "\n\n——夜间补记——\n" if source == DiarySource.NIGHTLY.value else "\n\n"
        remaining = max(0, 4000 - len(row.body) - len(sep))
        if len(body) > remaining:
            raise ValueError(f"当日日记还可追加 {remaining} 字符；本次内容尚未保存，请精简补记，原日记保持不变")
        row.body = row.body + sep + body
        if not row.title and title:
            row.title = title
        row.mood = mood or row.mood
        if moment_ids:
            row.moment_ids = list(dict.fromkeys((row.moment_ids or []) + moment_ids))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if _retried:
            raise
        return await upsert_diary(
            db,
            user_id,
            entry_date=entry_date,
            title=title,
            body=body,
            mood=mood,
            source=source,
            moment_ids=moment_ids,
            _retried=True,
        )
    await db.refresh(row)
    await _emit_event(user_id, "companion.diary.upserted", response_for_diary(row).model_dump())
    return row


async def collect_moment_interactions(
    db: AsyncSession,
    user_id: int,
    *,
    utc_start: datetime,
    utc_end: datetime,
) -> MomentInteractions:
    """汇总本地当日片刻互动：当日发布的片刻 + 当日有新评论的片刻，各带完整评论线程。供夜间规划、反思日记与日记投影共同消费，使片刻评论区成为伙伴反思上下文的一部分。"""
    posted_ids = list(
        (
            await db.scalars(
                select(CompanionMoment.id)
                .where(
                    CompanionMoment.user_id == user_id,
                    CompanionMoment.occurred_at >= utc_start,
                    CompanionMoment.occurred_at < utc_end,
                )
                .order_by(CompanionMoment.occurred_at.desc()),
            )
        ).all(),
    )
    commented_ids = (
        await db.scalars(
            select(CompanionMomentComment.moment_id)
            .where(
                CompanionMomentComment.user_id == user_id,
                CompanionMomentComment.created_at >= utc_start,
                CompanionMomentComment.created_at < utc_end,
            )
            .distinct(),
        )
    ).all()
    ids = list(dict.fromkeys([*posted_ids[:_INTERACTION_POSTED_LIMIT], *commented_ids]))
    if not ids:
        return MomentInteractions(threads=[], posted_ids=posted_ids)
    rows = (
        await db.scalars(
            select(CompanionMoment).where(CompanionMoment.id.in_(ids)).order_by(CompanionMoment.occurred_at.desc()),
        )
    ).all()
    threads = [
        {
            "title": m.title,
            "body": m.body,
            "kind": m.kind,
            "source": m.source,
            # call_llm_once 的 json.dumps 无 default，datetime 必须先转字符串
            "occurred_at": m.occurred_at.isoformat() if m.occurred_at else None,
            "posted_in_window": m.occurred_at is not None and utc_start <= m.occurred_at < utc_end,
            "comments": [
                {
                    "role": c.role,
                    "content": c.content,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "in_window": c.created_at is not None and utc_start <= c.created_at < utc_end,
                }
                for c in (m.comments or [])
            ],
        }
        for m in rows
    ]
    return MomentInteractions(threads=threads, posted_ids=posted_ids)


async def resolve_user_local_today(db: AsyncSession, user_id: int) -> date:
    """按用户已绑定的 IANA 时区换算本地日历日；无时区或未知时回退为 UTC 当日。"""
    tz = await resolve_user_timezone(db, user_id)
    if not tz:
        return utc_now().date()
    try:
        return utc_now().astimezone(ZoneInfo(tz)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return utc_now().date()


async def _emit_event(user_id: int, event_type: str, payload: dict[str, Any]) -> None:
    """内容提交后另起事务投递刷新事件；投递失败只记日志，已保存的内容不回滚。"""
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit journal event", extra={"event_type": event_type}, exc_info=True)
