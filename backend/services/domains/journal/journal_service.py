"""伙伴时刻 / 日记服务：精灵主导时间线的写入与节流、评论区、夜间批处理投影。

``memories`` 表不动；moments / diary 是给用户看的展示面，不是检索向量。
片刻完全由精灵发起（夜间规划、聊天工具、白天自主冲动），用户只能评论、隐藏。
"""

import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    SESSION_LOCAL,
    SETTINGS,
    download_capped,
    ensure_utc,
    get_file_path,
    get_logger,
    utc_now,
)
from modules.companion import (
    CompanionDiaryEntry,
    CompanionMoment,
    CompanionMomentComment,
    DiarySource,
    MomentCommentRole,
    MomentKind,
    MomentSource,
)
from modules.conversation import Message
from modules.ws import emit_ws_event
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import get_or_create_special_conversation
from services.domains.memory import resolve_user_timezone
from services.infrastructure.assets import (
    save_companion_asset,
    save_companion_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
)

logger = get_logger(__name__)


class JournalError(RuntimeError):
    pass


class MomentNotFoundError(JournalError):
    pass


class DiaryNotFoundError(JournalError):
    pass


async def persist_moment_media(user_id: int, media_identifier: str | None) -> str | None:
    """把片刻媒体固化为正式资产（``companion-assets/{user_id}/``）后返回裸路径。

    temp-media 来源转存接管；外部 http(s) 地址（供应商产物短时效）下载后转存，失败显式报错——
    时刻历史永不引用会过期的外部或临时地址，否则展示与备份都会留下死链。``data:`` 与既有资产原样返回。
    """
    if not media_identifier:
        return None
    raw = media_identifier.strip()
    if raw.startswith(("companion-assets/", "data:")):
        return raw
    if raw.startswith(("http://", "https://")) and "/api/media/files/" not in raw:
        data = await download_capped(
            raw,
            max_bytes=SETTINGS.journal_media_download_max_bytes,
            timeout=600.0,
        )
        ext = sniff_media_ext(data)
        if ext is None:
            raise JournalError("moment media is not a recognizable image, video or audio")
        return await save_companion_asset_async(data, user_id=user_id, label="moment_media", ext=ext)
    file_id = raw.split("/api/media/files/")[-1].split("?")[0].split("/")[0] if "/api/media/files/" in raw else raw
    resolved = get_file_path(file_id)
    if resolved is not None:
        path, _ = resolved
        try:
            return await asyncio.to_thread(_copy_temp_media, user_id, path)
        except OSError:
            logger.warning(
                "Failed to migrate temp media for moment",
                extra={"user_id": user_id, "file_id": file_id},
                exc_info=True,
            )
    raise JournalError(f"moment media unavailable: {file_id}")


def _copy_temp_media(user_id: int, path: Path) -> str:
    data = path.read_bytes()
    ext = path.suffix.lstrip(".").lower() or "png"
    return save_companion_asset(data, user_id=user_id, label="moment_media", ext=ext)


def _moment_media_type(media_identifier: str | None) -> str:
    if not media_identifier:
        return ""
    clean = media_identifier.split("?", 1)[0].lower()
    if clean.endswith((".mp4", ".webm", ".mov")):
        return "video"
    if clean.endswith((".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")):
        return "audio"
    return "image"


def response_for_comment(row: CompanionMomentComment) -> dict[str, Any]:
    return {
        "id": row.id,
        "moment_id": row.moment_id,
        "role": row.role,
        "content": row.content,
        "created_at": row.created_at,
    }


def response_for_moment(row: CompanionMoment) -> dict[str, Any]:
    url = row.media_url
    if url and url.startswith("companion-assets/"):
        url = signed_companion_asset_url(url) or url
    audio_url = row.audio_url
    if audio_url and audio_url.startswith("companion-assets/"):
        audio_url = signed_companion_asset_url(audio_url) or audio_url
    return {
        "id": row.id,
        "occurred_at": row.occurred_at,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "emotion": row.emotion,
        "media_url": url,
        "media_type": row.media_type or _moment_media_type(row.media_url),
        "audio_url": audio_url,
        "media_metadata": row.media_metadata,
        "source": row.source,
        "comments": [response_for_comment(c) for c in (row.comments or [])],
    }


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
    emotion: str | None = None,
    media_url: str | None = None,
    media_type: str | None = None,
    audio_url: str | None = None,
    media_metadata: dict[str, Any] | None = None,
    kind: str = MomentKind.EMOTION.value,
    source: str = MomentSource.NIGHTLY.value,
    session_id: int | None = None,
    memory_id: int | None = None,
) -> CompanionMoment:
    persisted_media = await persist_moment_media(user_id, media_url)
    persisted_audio = await persist_moment_media(user_id, audio_url)
    row = CompanionMoment(
        id=str(uuid4()),
        user_id=user_id,
        kind=kind,
        title=title.strip()[:64],
        body=body.strip()[:500],
        emotion=(emotion or None),
        media_url=persisted_media,
        media_type=media_type or _moment_media_type(persisted_media),
        audio_url=persisted_audio,
        media_metadata=media_metadata,
        source=source,
        session_id=session_id,
        memory_id=memory_id,
    )
    if source == MomentSource.NIGHTLY.value:
        conversation = await get_or_create_special_conversation(db, user_id, "companion")
        row.session_id = conversation.id
        media = [{"type": row.media_type, "url": persisted_media}] if persisted_media else []
        if persisted_audio and media:
            media[0]["audio_url"] = persisted_audio
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="\n\n".join(part for part in (row.title, row.body) if part),
            subtype="status_media" if media else "status_proactive",
            media_json=json.dumps(media, ensure_ascii=False) if media else None,
        )
        db.add(message)
        await db.flush()
        payload_media = [
            {
                **item,
                **{
                    key: signed_companion_asset_url(item[key]) or item[key]
                    for key in ("url", "audio_url")
                    if key in item
                },
            }
            for item in media
        ]
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.message",
            payload={
                "text": message.content,
                "session_id": str(conversation.id),
                "message_id": message.id,
                "media": payload_media,
            },
        )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    # 新建行未经查询，refresh 不填充 selectin 关系；显式置空避免事件序列化触发异步惰性加载。
    row.comments = []
    await _emit_moment_event(row)
    return row


async def create_generated_moment(
    db: AsyncSession,
    user_id: int,
    *,
    title: str,
    body: str,
    media_url: str,
    media_type: str | None = None,
    audio_url: str | None = None,
    media_metadata: dict[str, Any] | None = None,
    emotion: str | None = None,
    kind: str = MomentKind.TOGETHER.value,
    source: str = MomentSource.NIGHTLY.value,
) -> CompanionMoment:
    """把供应商临时 URL 转存为永久资产后写入可见时刻。"""
    persisted, detected_type = await _persist_generated_media(
        user_id,
        media_url,
        label="nightly_gift",
    )
    persisted_audio = None
    if audio_url:
        persisted_audio, _ = await _persist_generated_media(
            user_id,
            audio_url,
            label="nightly_voice",
        )
    return await create_user_moment(
        db,
        user_id,
        title=title,
        body=body,
        emotion=emotion,
        media_url=persisted,
        media_type=media_type or detected_type,
        audio_url=persisted_audio,
        media_metadata=media_metadata,
        kind=kind,
        source=source,
    )


async def _persist_generated_media(
    user_id: int,
    media_url: str,
    *,
    label: str,
) -> tuple[str, str]:
    if not media_url.startswith(("http://", "https://")):
        persisted = await persist_moment_media(user_id, media_url) or media_url
        return persisted, _moment_media_type(persisted)
    data = await download_capped(
        media_url,
        max_bytes=SETTINGS.journal_media_download_max_bytes,
        timeout=600.0,
    )
    ext = _generated_media_extension(data)
    persisted = await asyncio.to_thread(save_companion_asset, data, user_id=user_id, label=label, ext=ext)
    return persisted, _media_type_for_extension(ext)


def _generated_media_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"GIF8"):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp":
        return "mp4"
    raise ValueError("generated media has an unsupported file signature")


def _media_type_for_extension(ext: str) -> str:
    if ext == "mp4":
        return "video"
    if ext in ("mp3", "wav", "ogg", "m4a", "aac", "flac"):
        return "audio"
    return "image"


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
    moment = await get_moment(db, user_id, moment_id)
    if moment is None:
        raise MomentNotFoundError(f"moment {moment_id} not found")
    row = CompanionMomentComment(
        id=str(uuid4()),
        moment_id=moment.id,
        user_id=user_id,
        role=role,
        content=content.strip()[:500],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _emit_comment_event(row)
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


def response_for_diary(row: CompanionDiaryEntry) -> dict[str, Any]:
    return {
        "id": row.id,
        "entry_date": row.entry_date,
        "title": row.title,
        "body": row.body,
        "mood": row.mood,
        "source": row.source,
        "memory_ids": list(row.memory_ids or []),
        "moment_ids": list(row.moment_ids or []),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


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
    memory_ids: list[str] | None = None,
    moment_ids: list[str] | None = None,
    _retried: bool = False,
) -> CompanionDiaryEntry:
    row = await get_diary_by_date(db, user_id, entry_date)
    if row is None:
        row = CompanionDiaryEntry(
            id=str(uuid4()),
            user_id=user_id,
            entry_date=entry_date,
            title=title.strip()[:128],
            body=body.strip()[:2000],
            mood=mood,
            source=source,
            memory_ids=memory_ids or [],
            moment_ids=moment_ids or [],
        )
        db.add(row)
    else:
        # 同日已有日记时只追加不覆盖：夜间补记带专属分隔语，LLM 补记合并正文与标题。
        sep = "\n\n——夜间补记——\n" if source == DiarySource.NIGHTLY.value else "\n\n"
        row.body = (row.body + sep + body.strip()[:2000])[:4000]
        if not row.title and title:
            row.title = title.strip()[:128]
        row.mood = mood or row.mood
        if memory_ids:
            row.memory_ids = list(dict.fromkeys((row.memory_ids or []) + memory_ids))
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
            memory_ids=memory_ids,
            moment_ids=moment_ids,
            _retried=True,
        )
    await db.refresh(row)
    await _emit_diary_event(row)
    return row


async def collect_moment_interactions(
    db: AsyncSession,
    user_id: int,
    *,
    utc_start: datetime,
    utc_end: datetime,
) -> list[dict[str, Any]]:
    """汇总本地当日片刻互动：当日发布的片刻 + 当日有新评论的片刻，各带完整评论线程。

    供夜间规划、反思日记与日记投影共同消费，使片刻评论区成为伙伴反思上下文的一部分。
    """
    today_ids = (
        (
            await db.execute(
                select(CompanionMoment.id)
                .where(
                    CompanionMoment.user_id == user_id,
                    CompanionMoment.occurred_at >= utc_start,
                    CompanionMoment.occurred_at < utc_end,
                )
                .order_by(CompanionMoment.occurred_at.desc())
                .limit(30),
            )
        )
        .scalars()
        .all()
    )
    commented_ids = (
        (
            await db.execute(
                select(CompanionMomentComment.moment_id)
                .where(
                    CompanionMomentComment.user_id == user_id,
                    CompanionMomentComment.created_at >= utc_start,
                    CompanionMomentComment.created_at < utc_end,
                )
                .distinct(),
            )
        )
        .scalars()
        .all()
    )
    ids = list(dict.fromkeys([*today_ids, *commented_ids]))
    if not ids:
        return []
    rows = (
        (
            await db.execute(
                select(CompanionMoment).where(CompanionMoment.id.in_(ids)).order_by(CompanionMoment.occurred_at.desc()),
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "title": m.title,
            "body": m.body,
            "kind": m.kind,
            "source": m.source,
            # call_llm_once 的 json.dumps 无 default，datetime 必须先转字符串
            "occurred_at": m.occurred_at.isoformat() if m.occurred_at else None,
            "comments": [{"role": c.role, "content": c.content} for c in (m.comments or [])],
        }
        for m in rows
    ]


async def resolve_user_local_today(db: AsyncSession | None, user_id: int) -> date:
    """按用户已绑定的 IANA 时区换算本地日历日；无时区或未知时回退为 UTC 当日。"""
    if db is not None:
        tz = await resolve_user_timezone(db, user_id)
    else:
        async with SESSION_LOCAL() as session:
            tz = await resolve_user_timezone(session, user_id)
    if not tz:
        return utc_now().date()
    try:
        return utc_now().astimezone(ZoneInfo(tz)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return utc_now().date()


async def _emit_moment_event(row: CompanionMoment) -> None:
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(
                db,
                user_id=row.user_id,
                event_type="companion.moment.created",
                payload=response_for_moment(row),
            )
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.moment.created", exc_info=True)


async def _emit_comment_event(row: CompanionMomentComment) -> None:
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(
                db,
                user_id=row.user_id,
                event_type="companion.moment.comment",
                payload={"moment_id": row.moment_id, "comment": response_for_comment(row)},
            )
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.moment.comment", exc_info=True)


async def _emit_diary_event(row: CompanionDiaryEntry) -> None:
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(
                db,
                user_id=row.user_id,
                event_type="companion.diary.upserted",
                payload=response_for_diary(row),
            )
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.diary.upserted", exc_info=True)
