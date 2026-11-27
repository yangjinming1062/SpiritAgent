"""按账户和本地日期发布、读取日记。"""

from collections.abc import Sequence
from datetime import date

from infrastructure.domain_locks import lock_domain
from infrastructure.models import id_in
from runtime.events.emit import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .diary_search import index_diary, search_diary_entries
from .models import DiaryEntry
from .queries import date_scoped_diaries
from .schemas import DiaryContent, DiaryEntryResponse, DiarySearchResponse, DiaryUnreadResponse


async def list_diary(
    db: AsyncSession, user_id: int, *, date_from: date | None = None, date_to: date | None = None, limit: int = 100
) -> list[DiaryEntryResponse]:
    rows = await db.scalars(
        date_scoped_diaries(user_id, date_from, date_to)
        .order_by(DiaryEntry.entry_date.desc())
        .limit(max(1, min(365, limit)))
    )
    return [DiaryEntryResponse.model_validate(row) for row in rows]


async def get_diary_by_date(db: AsyncSession, user_id: int, entry_date: date) -> DiaryEntryResponse | None:
    row = await db.scalar(select(DiaryEntry).where(DiaryEntry.user_id == user_id, DiaryEntry.entry_date == entry_date))
    return DiaryEntryResponse.model_validate(row) if row is not None else None


async def publish_diary(
    db: AsyncSession, user_id: int, *, entry_date: date, content: DiaryContent
) -> DiaryEntryResponse:
    await lock_domain(db, user_id, "journal")
    row = await db.scalar(
        insert(DiaryEntry)
        .values(user_id=user_id, entry_date=entry_date, title=content.title, body=content.body)
        .on_conflict_do_nothing(constraint="uq_diary_entries_user_date")
        .returning(DiaryEntry)
    )
    if row is None:
        existing = await get_diary_by_date(db, user_id, entry_date)
        if existing is None:
            raise RuntimeError("Conflicting diary disappeared")
        return existing
    await index_diary(db, row)
    response = DiaryEntryResponse.model_validate(row)
    emit_ws_event(db, user_id=user_id, event_type="companion.diary.created", payload=response.model_dump(mode="json"))
    return response


async def unread_diary_ids(db: AsyncSession, user_id: int) -> list[int]:
    ids = await db.scalars(select(DiaryEntry.id).where(DiaryEntry.user_id == user_id, DiaryEntry.is_read.is_(False)))
    return list(ids)


async def search_diary(
    user_id: int, *, query: str = "", date_from: date | None = None, date_to: date | None = None, limit: int = 20
) -> DiarySearchResponse:
    rows, status = await search_diary_entries(user_id, query=query, date_from=date_from, date_to=date_to, limit=limit)
    return DiarySearchResponse(entries=[DiaryEntryResponse.model_validate(row) for row in rows], search=status)


async def diary_read_state(db: AsyncSession, user_id: int) -> DiaryUnreadResponse:
    ids = await unread_diary_ids(db, user_id)
    return DiaryUnreadResponse(has_unread=bool(ids), unread_diary_ids=ids)


async def mark_diary_read(db: AsyncSession, user_id: int, diary_ids: Sequence[int]) -> DiaryUnreadResponse:
    if diary_ids:
        changed = await db.execute(
            update(DiaryEntry)
            .where(DiaryEntry.user_id == user_id, id_in(DiaryEntry.id, diary_ids), DiaryEntry.is_read.is_(False))
            .values(is_read=True)
        )
        if changed.rowcount:
            emit_ws_event(db, user_id=user_id, event_type="companion.diary.read", payload={})
    state = await diary_read_state(db, user_id)
    await db.commit()
    return state
