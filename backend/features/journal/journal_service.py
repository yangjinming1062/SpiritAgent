"""日记发布和读取；内容及引用由 entries 领域持有。"""

from collections.abc import Sequence
from datetime import date

from infrastructure.domain_locks import lock_domain
from runtime.events.emit import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

from features.entries.contracts import EntryView
from features.entries.queries import diary_for_date, has_unread_entries, list_diary_entries, unread_entry_ids
from features.entries.service import create_diary, mark_entries_read
from features.journal.schemas import DiaryContent, DiaryEntryResponse

from .journal_indexes import index_entry


def response_for_diary(row: EntryView | DiaryEntryResponse) -> DiaryEntryResponse:
    return DiaryEntryResponse.model_validate(row, from_attributes=True)


async def list_diary(
    db: AsyncSession, user_id: int, *, date_from: date | None = None, date_to: date | None = None, limit: int = 100
) -> list[DiaryEntryResponse]:
    return [
        response_for_diary(row)
        for row in await list_diary_entries(db, user_id, date_from=date_from, date_to=date_to, limit=limit)
    ]


async def get_diary_by_date(db: AsyncSession, user_id: int, entry_date: date) -> DiaryEntryResponse | None:
    row = await diary_for_date(db, user_id, entry_date)
    return response_for_diary(row) if row else None


async def publish_diary(
    db: AsyncSession, user_id: int, *, entry_date: date, content: DiaryContent, post_ids: list[int]
) -> DiaryEntryResponse:
    await lock_domain(db, user_id, "entries")
    row, created = await create_diary(
        db, user_id, entry_date=entry_date, title=content.title, body=content.body, post_ids=post_ids
    )
    if created:
        await index_entry(db, row)
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.diary.created",
            payload=response_for_diary(row).model_dump(mode="json"),
        )
    return response_for_diary(row)


async def unread_diary_ids(db: AsyncSession, user_id: int) -> list[int]:
    return await unread_entry_ids(db, user_id, "diary")


async def has_unread_diary(db: AsyncSession, user_id: int) -> bool:
    return await has_unread_entries(db, user_id, "diary")


async def mark_diary_read(db: AsyncSession, user_id: int, diary_ids: Sequence[int]) -> bool:
    unread = await mark_entries_read(db, user_id, "diary", diary_ids)
    await db.commit()
    return unread
