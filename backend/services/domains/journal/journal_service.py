"""日记发布、账户未读状态与原文查询。"""

from collections.abc import Sequence
from datetime import date
from uuid import uuid4

from modules.auth import lock_user_row
from modules.companion import CompanionDiaryEntry, DiaryContent, DiaryEntryResponse
from modules.ws import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.memory import index_diary_memory


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
    stmt = select(CompanionDiaryEntry).where(CompanionDiaryEntry.user_id == user_id)
    if date_from is not None:
        stmt = stmt.where(CompanionDiaryEntry.entry_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(CompanionDiaryEntry.entry_date <= date_to)
    return list(
        (await db.scalars(stmt.order_by(CompanionDiaryEntry.entry_date.desc()).limit(max(1, min(365, limit))))).all(),
    )


async def get_diary_by_date(db: AsyncSession, user_id: int, entry_date: date) -> CompanionDiaryEntry | None:
    return await db.scalar(
        select(CompanionDiaryEntry).where(
            CompanionDiaryEntry.user_id == user_id,
            CompanionDiaryEntry.entry_date == entry_date,
        ),
    )


async def publish_diary(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: date,
    content: DiaryContent,
    post_ids: list[str],
) -> CompanionDiaryEntry:
    """调用方提交事务；已有日记保持原文、已读状态与发布事件不变。"""
    await lock_user_row(db, user_id)
    existing = await get_diary_by_date(db, user_id, entry_date)
    if existing is not None:
        return existing
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
    await index_diary_memory(db, row)
    await db.refresh(row)
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.diary.created",
        payload=response_for_diary(row).model_dump(),
    )
    return row


async def unread_diary_ids(db: AsyncSession, user_id: int) -> list[str]:
    return list(
        (
            await db.scalars(
                select(CompanionDiaryEntry.id).where(
                    CompanionDiaryEntry.user_id == user_id,
                    CompanionDiaryEntry.is_read.is_(False),
                ),
            )
        ).all(),
    )


async def has_unread_diary(db: AsyncSession, user_id: int) -> bool:
    return bool(
        await db.scalar(
            select(
                select(CompanionDiaryEntry.id)
                .where(CompanionDiaryEntry.user_id == user_id, CompanionDiaryEntry.is_read.is_(False))
                .exists(),
            ),
        ),
    )


async def mark_diary_read(db: AsyncSession, user_id: int, diary_ids: Sequence[str]) -> bool:
    await lock_user_row(db, user_id)
    result = await db.execute(
        update(CompanionDiaryEntry)
        .where(
            CompanionDiaryEntry.user_id == user_id,
            CompanionDiaryEntry.id.in_(diary_ids),
            CompanionDiaryEntry.is_read.is_(False),
        )
        .values(is_read=True),
    )
    if result.rowcount:
        emit_ws_event(db, user_id=user_id, event_type="companion.diary.read", payload={})
    has_unread = await has_unread_diary(db, user_id)
    await db.commit()
    return has_unread
