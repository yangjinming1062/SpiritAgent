"""伙伴日记 / 时刻服务：CRUD + 节流 + 系统自动写 + 夜间批处理投影。

``memories`` 表不动；moments / diary 是给用户看的展示面，不是检索向量。
"""

from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import SESSION_LOCAL, SETTINGS, ensure_utc, get_file_path, get_logger, utc_now
from modules.companion import (
    CompanionDiaryEntry,
    CompanionMoment,
    DiarySource,
    MomentKind,
    MomentSource,
    MomentVisibility,
)
from modules.ws import emit_ws_event
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .asset_store import save_companion_asset, signed_companion_asset_url
from .memory_bootstrap import resolve_user_timezone

logger = get_logger(__name__)

# 系统时刻文案模板（每用户每天 ≤ moment.system_per_day；事件级别由调用方路由）。
_SYSTEM_MOMENT_TEMPLATES: dict[str, dict[str, str]] = {
    "greeting": {
        "title": "第一次见面",
        "body": "今天我们正式见面啦，以后请多关照。",
    },
    "milestone_outfit": {
        "title": "更换外观",
        "body": "",
    },
    "scene_room": {
        "title": "生活空间焕新",
        "body": "",
    },
}


class JournalError(RuntimeError):
    pass


class MomentNotFoundError(JournalError):
    pass


class DiaryNotFoundError(JournalError):
    pass


def persist_moment_media(user_id: int, media_identifier: str | None) -> str | None:
    """若媒体来自 temp-media，转存至正式资产目录 companion-assets/{user_id}/；外部 URL 或既有 asset 原样返回。"""
    if not media_identifier:
        return None
    raw = media_identifier.strip()
    if raw.startswith("companion-assets/") or raw.startswith(("http://", "https://", "data:")):
        return raw
    file_id = raw.split("/api/media/files/")[-1].split("?")[0].split("/")[0] if "/api/media/files/" in raw else raw
    resolved = get_file_path(file_id)
    if resolved is not None:
        path, _ = resolved
        try:
            data = path.read_bytes()
            ext = path.suffix.lstrip(".").lower() or "png"
            return save_companion_asset(data, user_id=user_id, label="moment_media", ext=ext)
        except OSError:
            logger.warning("Failed to migrate temp media for moment", extra={"user_id": user_id, "file_id": file_id}, exc_info=True)
    return raw


def response_for_moment(row: CompanionMoment) -> dict[str, Any]:
    url = row.media_url
    if url and url.startswith("companion-assets/"):
        url = signed_companion_asset_url(url) or url
    return {
        "id": row.id,
        "occurred_at": row.occurred_at,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "emotion": row.emotion,
        "media_url": url,
        "source": row.source,
        "visibility": row.visibility,
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
        .where(CompanionMoment.user_id == user_id, CompanionMoment.visibility == MomentVisibility.SHOWN.value)
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
    kind: str = MomentKind.USER.value,
    source: str = MomentSource.USER.value,
    session_id: int | None = None,
    memory_id: int | None = None,
) -> CompanionMoment:
    persisted_media = persist_moment_media(user_id, media_url)
    row = CompanionMoment(
        id=str(uuid4()),
        user_id=user_id,
        kind=kind,
        title=title.strip()[:64],
        body=body.strip()[:500],
        emotion=(emotion or None),
        media_url=persisted_media,
        source=source,
        session_id=session_id,
        memory_id=memory_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _emit_moment_event(row)
    return row


async def update_moment(
    db: AsyncSession,
    user_id: int,
    moment_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    visibility: str | None = None,
) -> CompanionMoment:
    row = (
        await db.execute(
            select(CompanionMoment).where(CompanionMoment.id == moment_id, CompanionMoment.user_id == user_id),
        )
    ).scalar_one_or_none()
    if row is None:
        raise MomentNotFoundError(f"moment {moment_id} not found")
    if title is not None:
        row.title = title.strip()[:64]
    if body is not None:
        row.body = body.strip()[:500]
    if visibility is not None:
        row.visibility = visibility
    await db.commit()
    await db.refresh(row)
    return row


async def soft_delete_moment(db: AsyncSession, user_id: int, moment_id: str) -> None:
    row = (
        await db.execute(
            select(CompanionMoment).where(CompanionMoment.id == moment_id, CompanionMoment.user_id == user_id),
        )
    ).scalar_one_or_none()
    if row is None:
        raise MomentNotFoundError(f"moment {moment_id} not found")
    row.visibility = MomentVisibility.HIDDEN.value
    await db.commit()


async def write_system_moment(
    user_id: int,
    *,
    kind: str,
    event_key: str,
    title: str | None = None,
    body: str | None = None,
    media_url: str | None = None,
    emotion: str | None = None,
    session_id: int | None = None,
    memory_id: int | None = None,
) -> CompanionMoment | None:
    if not user_id:
        return None
    limit = int(SETTINGS.moment_system_per_day)
    since = utc_now() - timedelta(hours=24)
    template = _SYSTEM_MOMENT_TEMPLATES.get(event_key, {})
    final_title = (title if title is not None else template.get("title", "片段"))[:64]
    final_body = (body if body is not None else template.get("body", ""))[:500]
    persisted_media = persist_moment_media(user_id, media_url)
    async with SESSION_LOCAL() as session:
        existing = (
            (
                await session.execute(
                    select(CompanionMoment.title).where(
                        CompanionMoment.user_id == user_id,
                        CompanionMoment.source == MomentSource.SYSTEM.value,
                        CompanionMoment.created_at >= since,
                    ),
                )
            )
            .scalars()
            .all()
        )
        if final_title in existing:
            return None
        if len(existing) >= limit:
            logger.info("journal: system moment daily cap hit", extra={"user_id": user_id, "limit": limit})
            return None
        row = CompanionMoment(
            id=str(uuid4()),
            user_id=user_id,
            kind=kind,
            title=final_title,
            body=final_body,
            emotion=emotion,
            media_url=persisted_media,
            source=MomentSource.SYSTEM.value,
            session_id=session_id,
            memory_id=memory_id,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        await _emit_moment_event(row)
        return row


async def check_moment_llm_quota(db: AsyncSession, user_id: int) -> bool:
    """角色主动 moment_create 每日配额：每用户每 24h ≤ moment_llm_per_day（默认 3）。"""
    limit = int(SETTINGS.moment_llm_per_day)
    if limit <= 0:
        return True
    since = utc_now() - timedelta(hours=24)
    count = (
        await db.execute(
            select(func.count(CompanionMoment.id)).where(
                CompanionMoment.user_id == user_id,
                CompanionMoment.source == MomentSource.LLM.value,
                CompanionMoment.created_at >= since,
            ),
        )
    ).scalar_one()
    return count < limit


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
        "edited_at": row.edited_at,
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
            select(CompanionDiaryEntry).where(CompanionDiaryEntry.user_id == user_id, CompanionDiaryEntry.entry_date == entry_date),
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
    source: str = DiarySource.USER.value,
    memory_ids: list[str] | None = None,
    moment_ids: list[str] | None = None,
    edited_at: datetime | None = None,
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
            edited_at=edited_at,
        )
        db.add(row)
    else:
        is_user_edited = row.source == DiarySource.USER.value or row.edited_at is not None
        if source == DiarySource.NIGHTLY.value and is_user_edited:
            sep = "\n\n——夜间补记——\n"
            row.body = (row.body + sep + body.strip()[:2000])[:4000]
            if not row.title and title:
                row.title = title.strip()[:128]
            row.mood = mood or row.mood
        elif source == DiarySource.LLM.value:
            sep = "\n\n——伙伴补记——\n" if is_user_edited else "\n\n"
            row.body = (row.body + sep + body.strip()[:2000])[:4000]
            if not row.title and title:
                row.title = title.strip()[:128]
            row.mood = mood or row.mood
        else:
            row.title = title.strip()[:128] or row.title
            row.body = body.strip()[:2000]
            row.mood = mood or row.mood
            row.source = source
            row.edited_at = edited_at
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
            edited_at=edited_at,
            _retried=True,
        )
    await db.refresh(row)
    await _emit_diary_event(row)
    return row


async def create_user_diary(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: date | None = None,
    title: str | None,
    body: str,
    mood: str | None = None,
) -> CompanionDiaryEntry:
    target_date = entry_date or (await resolve_user_local_today(db, user_id))
    return await upsert_diary(
        db,
        user_id,
        entry_date=target_date,
        title=title or "",
        body=body,
        mood=mood,
        source=DiarySource.USER.value,
        edited_at=utc_now(),
    )


async def update_diary(
    db: AsyncSession,
    user_id: int,
    diary_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    mood: str | None = None,
) -> CompanionDiaryEntry:
    row = (
        await db.execute(
            select(CompanionDiaryEntry).where(CompanionDiaryEntry.id == diary_id, CompanionDiaryEntry.user_id == user_id),
        )
    ).scalar_one_or_none()
    if row is None:
        raise DiaryNotFoundError(f"diary {diary_id} not found")
    if title is not None:
        row.title = title.strip()[:128]
    if body is not None:
        row.body = body.strip()[:2000]
    if mood is not None:
        row.mood = mood
    row.source = DiarySource.USER.value
    row.edited_at = utc_now()
    await db.commit()
    await db.refresh(row)
    await _emit_diary_event(row)
    return row


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
            emit_ws_event(db, user_id=row.user_id, event_type="companion.moment.created", payload=response_for_moment(row))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.moment.created", exc_info=True)


async def _emit_diary_event(row: CompanionDiaryEntry) -> None:
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=row.user_id, event_type="companion.diary.upserted", payload=response_for_diary(row))
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.diary.upserted", exc_info=True)
