"""日记发布、账户未读状态与原文查询。"""

from collections.abc import Sequence
from datetime import date
from uuid import uuid4

from infrastructure.read_state import ReadStateTable, has_unread, mark_read, unread_ids
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account
from features.journal.models import CompanionDiaryEntry
from features.journal.schemas import DiaryContent, DiaryEntryResponse

from .journal_indexes import index_entry


def response_for_diary(row: CompanionDiaryEntry | DiaryEntryResponse) -> DiaryEntryResponse:
    return DiaryEntryResponse.model_validate(row, from_attributes=True)


async def list_diary(
    db: AsyncSession,
    user_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 100,
) -> list[DiaryEntryResponse]:
    stmt = select(CompanionDiaryEntry).where(CompanionDiaryEntry.user_id == user_id)
    if date_from is not None:
        stmt = stmt.where(CompanionDiaryEntry.entry_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(CompanionDiaryEntry.entry_date <= date_to)
    rows = await db.scalars(stmt.order_by(CompanionDiaryEntry.entry_date.desc()).limit(max(1, min(365, limit))))
    return [response_for_diary(row) for row in rows]


async def _get_diary_by_date(db: AsyncSession, user_id: int, entry_date: date) -> CompanionDiaryEntry | None:
    return await db.scalar(
        select(CompanionDiaryEntry).where(
            CompanionDiaryEntry.user_id == user_id,
            CompanionDiaryEntry.entry_date == entry_date,
        ),
    )


async def get_diary_by_date(db: AsyncSession, user_id: int, entry_date: date) -> DiaryEntryResponse | None:
    row = await _get_diary_by_date(db, user_id, entry_date)
    return response_for_diary(row) if row is not None else None


async def publish_diary(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: date,
    content: DiaryContent,
    post_ids: list[str],
) -> DiaryEntryResponse:
    """调用方提交事务；已有日记保持原文、已读状态与发布事件不变。"""
    await lock_account(db, user_id)
    existing = await _get_diary_by_date(db, user_id, entry_date)
    if existing is not None:
        return response_for_diary(existing)
    row = CompanionDiaryEntry(
        id=str(uuid4()),
        user_id=user_id,
        entry_date=entry_date,
        **content.model_dump(),
        post_ids=list(dict.fromkeys(post_ids)),
        is_read=False,
    )
    db.add(row)
    await db.flush()
    await index_entry(db, row)
    await db.refresh(row)
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.diary.created",
        payload=response_for_diary(row).model_dump(),
    )
    return response_for_diary(row)


async def unread_diary_ids(db: AsyncSession, user_id: int) -> list[str]:
    return await unread_ids(db, user_id, ReadStateTable(CompanionDiaryEntry))


async def has_unread_diary(db: AsyncSession, user_id: int) -> bool:
    return await has_unread(db, user_id, ReadStateTable(CompanionDiaryEntry))


async def mark_diary_read(db: AsyncSession, user_id: int, diary_ids: Sequence[str]) -> bool:
    return await mark_read(
        db,
        user_id,
        ReadStateTable(CompanionDiaryEntry),
        diary_ids,
        lock_account=lock_account,
        notify=lambda: emit_ws_event(db, user_id=user_id, event_type="companion.diary.read", payload={}),
    )
