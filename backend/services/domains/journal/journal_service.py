"""日记写入、追加容量与本地日期。"""

from datetime import date
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import utc_now
from modules.companion import CompanionDiaryEntry, DiaryEntryResponse, DiarySource
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.memory import resolve_user_timezone


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


_MOOD_MAX_CHARS = 32
_DIARY_MAX_CHARS = 4000
_NIGHTLY_APPEND_SEPARATOR = "\n\n——夜间补记——\n"


def _append_separator(source: str) -> str:
    return _NIGHTLY_APPEND_SEPARATOR if source == DiarySource.NIGHTLY.value else "\n\n"


async def diary_append_capacity(db: AsyncSession, user_id: int, entry_date: date, *, source: str) -> tuple[str, int]:
    """返回同日已有正文与本次最多可写入的字符数；没有日记时正文为空、可写满单次上限。"""
    row = await get_diary_by_date(db, user_id, entry_date)
    if row is None:
        return "", 2000
    return row.body, min(2000, max(0, _DIARY_MAX_CHARS - len(row.body) - len(_append_separator(source))))


async def upsert_diary(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: date,
    title: str,
    body: str,
    mood: str | None = None,
    source: str,
    post_ids: list[str] | None = None,
    _retried: bool = False,
) -> CompanionDiaryEntry:
    title, body = title.strip(), body.strip()
    if len(title) > 128 or not body or len(body) > 2000:
        raise ValueError("日记标题最多 128 字符，本次正文须为 1–2000 字符；内容尚未保存")
    if mood is not None and len(mood) > _MOOD_MAX_CHARS:
        raise ValueError(f"日记心情最多 {_MOOD_MAX_CHARS} 字符；内容尚未保存")
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
            post_ids=post_ids or [],
        )
        db.add(row)
    else:
        # 同日正文只追加，已有标题不覆盖；夜间补记使用专属分隔语。
        sep = _append_separator(source)
        remaining = max(0, _DIARY_MAX_CHARS - len(row.body) - len(sep))
        if len(body) > remaining:
            raise ValueError(f"当日日记还可追加 {remaining} 字符；本次内容尚未保存，请精简补记，原日记保持不变")
        row.body = row.body + sep + body
        if not row.title and title:
            row.title = title
        row.mood = mood or row.mood
        if post_ids:
            row.post_ids = list(dict.fromkeys((row.post_ids or []) + post_ids))
    try:
        await db.flush()
    except IntegrityError:
        # 同日并发首写撞唯一约束：回滚后按已存在的日记重走追加路径。
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
            post_ids=post_ids,
            _retried=True,
        )
    await db.refresh(row)
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.diary.upserted",
        payload=response_for_diary(row).model_dump(),
    )
    await db.commit()
    return row


async def resolve_user_local_today(db: AsyncSession, user_id: int) -> date:
    """按用户已绑定的 IANA 时区换算本地日历日；无时区或未知时回退为 UTC 当日。"""
    tz = await resolve_user_timezone(db, user_id)
    if not tz:
        return utc_now().date()
    try:
        return utc_now().astimezone(ZoneInfo(tz)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return utc_now().date()
