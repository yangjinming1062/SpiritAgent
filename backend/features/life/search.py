"""生活事件与阅读所得的独立检索。"""

import json
from collections.abc import Sequence
from datetime import date

from infrastructure.database import session_scope
from infrastructure.search import SearchStatus, embed_search_query, local_date_bounds
from infrastructure.search_index import SearchDocument, search_documents, upsert_search_documents
from pydantic import BaseModel
from runtime.search_indexing import SearchIndexWorker
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.timezone import resolve_user_timezone

from .actions import ReadingResultView
from .models import LifeEvent, LifeEventSearchIndex, LifeStep
from .prompts import life_content_payload
from .queries import event_view, step_view
from .schemas import LifeEventPublicView, LifeEventView, LifeTrajectoryEntry
from .trajectory import trajectory_entries, trajectory_scope

LIFE_SEARCH_WORKER = SearchIndexWorker("life.search", LifeEventSearchIndex)


class LifeRecallItem(LifeEventView):
    reading_result: ReadingResultView | None = None


class LifeSearchItem(LifeEventPublicView):
    reading_result: ReadingResultView | None = None


class LifeRecallResponse(BaseModel):
    items: list[LifeRecallItem]
    search: SearchStatus


class LifeSearchResponse(BaseModel):
    items: list[LifeSearchItem]
    search: SearchStatus


class LifeTrajectorySearchResponse(BaseModel):
    items: list[LifeTrajectoryEntry]
    search: SearchStatus


async def _recall_items(db: AsyncSession, user_id: int, events: Sequence[LifeEvent]) -> list[LifeRecallItem]:
    step_ids = {event.step_id for event in events if event.step_id is not None}
    steps = (
        {
            row.id: step_view(row).reading_result
            for row in await db.scalars(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id.in_(step_ids)))
        }
        if step_ids
        else {}
    )
    return [LifeRecallItem(**event_view(row).model_dump(), reading_result=steps.get(row.step_id)) for row in events]


async def index_life_events(db: AsyncSession, user_id: int, events: Sequence[LifeEvent]) -> None:
    items = await _recall_items(db, user_id, [event for event in events if event.is_meaningful])
    await upsert_search_documents(
        db,
        LifeEventSearchIndex,
        user_id,
        [
            SearchDocument(
                item.id, json.dumps(life_content_payload(item.prompt_payload()), ensure_ascii=False, sort_keys=True)
            )
            for item in items
        ],
    )


async def rebuild_life_search(db: AsyncSession, user_id: int) -> None:
    """只补缺失文本；事件不可改写，覆盖恢复通过来源级联撤销旧索引。"""
    indexed = exists().where(LifeEventSearchIndex.user_id == user_id, LifeEventSearchIndex.record_id == LifeEvent.id)
    while True:
        events = list(
            await db.scalars(
                select(LifeEvent)
                .where(LifeEvent.user_id == user_id, LifeEvent.is_meaningful.is_(True), ~indexed)
                .order_by(LifeEvent.id)
                .limit(100)
            )
        )
        if not events:
            return
        await index_life_events(db, user_id, events)


async def _search_events(
    db: AsyncSession,
    user_id: int,
    *,
    query: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    pursuit_id: int | None = None,
    topic_id: int | None = None,
    trajectory_only: bool = True,
    shared_only: bool = False,
    limit: int = 20,
) -> tuple[list[LifeEvent], SearchStatus]:
    if not 1 <= limit <= 50 or len(query) > 1000:
        raise ValueError("检索数量须为 1–50，搜索词不超过 1000 字符")
    query = query.strip()
    timezone = await resolve_user_timezone(db, user_id) or "UTC"
    start, end = local_date_bounds(date_from, date_to, timezone)
    await db.rollback()
    vector = await embed_search_query(user_id, query)
    where = [LifeEvent.user_id == user_id, LifeEvent.is_meaningful.is_(True)]
    if shared_only:
        where.append(LifeEvent.shared_with_user.is_(True))
    if trajectory_only:
        where.append(trajectory_scope(pursuit_id=pursuit_id, topic_id=topic_id, shared_only=shared_only))
    elif pursuit_id is not None:
        where.append(LifeEvent.pursuit_id == pursuit_id)
    if start is not None:
        where.append(LifeEvent.occurred_at >= start)
    if end is not None:
        where.append(LifeEvent.occurred_at < end)
    if query:
        await rebuild_life_search(db, user_id)
        await db.commit()
        ids, status = await search_documents(
            db, LifeEventSearchIndex, user_id, select(LifeEvent.id).where(*where), query, vector=vector, limit=limit
        )
        found = {row.id: row for row in await db.scalars(select(LifeEvent).where(*where, LifeEvent.id.in_(ids)))}
        LIFE_SEARCH_WORKER.schedule(user_id, vector)
        return [found[identifier] for identifier in ids if identifier in found], status
    rows = await db.scalars(
        select(LifeEvent).where(*where).order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()).limit(limit)
    )
    return list(rows), SearchStatus(mode="timeline")


async def recall_life(
    user_id: int,
    *,
    query: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    pursuit_id: int | None = None,
    trajectory_only: bool = True,
    limit: int = 20,
) -> LifeRecallResponse:
    async with session_scope() as db:
        rows, status = await _search_events(
            db,
            user_id,
            query=query,
            date_from=date_from,
            date_to=date_to,
            pursuit_id=pursuit_id,
            trajectory_only=trajectory_only,
            limit=limit,
        )
        return LifeRecallResponse(items=await _recall_items(db, user_id, rows), search=status)


async def search_life(
    user_id: int,
    *,
    query: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    pursuit_id: int | None = None,
    limit: int = 20,
) -> LifeSearchResponse:
    async with session_scope() as db:
        rows, status = await _search_events(
            db,
            user_id,
            query=query,
            date_from=date_from,
            date_to=date_to,
            pursuit_id=pursuit_id,
            trajectory_only=False,
            shared_only=True,
            limit=limit,
        )
        return LifeSearchResponse.model_validate(
            LifeRecallResponse(items=await _recall_items(db, user_id, rows), search=status), from_attributes=True
        )


async def search_trajectory(
    user_id: int,
    *,
    query: str,
    date_from: date | None,
    date_to: date | None,
    pursuit_id: int | None,
    topic_id: int | None,
    limit: int,
) -> LifeTrajectorySearchResponse:
    async with session_scope() as db:
        rows, status = await _search_events(
            db,
            user_id,
            query=query,
            date_from=date_from,
            date_to=date_to,
            pursuit_id=pursuit_id,
            topic_id=topic_id,
            shared_only=True,
            limit=limit,
        )
        return LifeTrajectorySearchResponse(items=await trajectory_entries(db, user_id, rows), search=status)
