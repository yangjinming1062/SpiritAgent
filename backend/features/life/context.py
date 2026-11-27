"""生活领域的统一只读上下文。

聊天、影像和日记通过这里取得同源的生活状态、持续事项和近期事实。
"""

from collections.abc import Collection
from datetime import datetime

from infrastructure.search import extract_search_terms
from infrastructure.text import format_local_iso, utc_now
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.timezone import resolve_user_timezone

from .models import LifeEvent, LifeGrowth, LifePursuit
from .queries import (
    completed_personal_works,
    empty_state_view,
    event_view,
    get_life_state_view,
    pursuit_view,
    visible_completion_results,
)
from .schemas import LifeContext, PersonalGrowth
from .trajectory import trajectory_scope, trajectory_topic_heads


async def read_life_context_in_session(
    db: AsyncSession,
    user_id: int,
    *,
    growth_query: str = "",
    pursuit_limit: int = 5,
    event_limit: int | None = 12,
    include_topic_heads: bool = False,
    preferred_events: Collection[int] = (),
    preferred_pursuits: Collection[int] = (),
    preferred_growth: int | None = None,
    event_start: datetime | None = None,
    event_end: datetime | None = None,
    publication_events_only: bool = False,
) -> LifeContext:
    """在调用方事务内读取一份一致的生活快照。"""

    state = await get_life_state_view(db, user_id) or empty_state_view(user_id)
    terms = extract_search_terms(" ".join((growth_query, state.current_activity)))
    pursuit_rows = []
    for status in ("active", "paused"):
        priority = (
            LifePursuit.id == state.active_pursuit_id
            if status == "active"
            else (LifePursuit.status_source == "companion")
            & or_(LifePursuit.resume_after.is_(None), LifePursuit.resume_after <= utc_now())
        )
        query = select(LifePursuit).where(LifePursuit.user_id == user_id, LifePursuit.status == status)
        if status == "active" and state.active_pursuit_id is not None:
            query = query.order_by(case((priority, 0), else_=1))
        if preferred_pursuits:
            query = query.order_by(
                case(
                    {i: rank for rank, i in enumerate(preferred_pursuits)},
                    value=LifePursuit.id,
                    else_=len(preferred_pursuits),
                )
            )
        if status == "paused":
            query = query.order_by(case((priority, 0), else_=1))
        if terms:
            query = query.order_by(
                or_(
                    *(
                        field.icontains(term, autoescape=True)
                        for term in terms
                        for field in (LifePursuit.title, LifePursuit.desired_result)
                    )
                ).desc()
            )
        pursuit_rows.extend(
            await db.scalars(query.order_by(LifePursuit.updated_at.desc(), LifePursuit.id.desc()).limit(pursuit_limit))
        )
    criteria = [LifeEvent.user_id == user_id, trajectory_scope(shared_only=publication_events_only)]
    if publication_events_only:
        criteria.append(LifeEvent.source != "life_share")
    if event_start is not None:
        criteria.append(LifeEvent.occurred_at >= event_start)
    if event_end is not None:
        criteria.append(LifeEvent.occurred_at < event_end)
    event_query = select(LifeEvent).where(*criteria)
    if preferred_events:
        event_query = event_query.order_by(
            case({i: rank for rank, i in enumerate(preferred_events)}, value=LifeEvent.id, else_=len(preferred_events))
        )
    events = await db.scalars(
        event_query.order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()).limit(event_limit)
    )
    terms = extract_search_terms(" ".join((growth_query, state.current_activity, *(row.title for row in pursuit_rows))))
    ranking = [LifeGrowth.updated_at.desc(), LifeGrowth.id.desc()]
    if terms:
        ranking.insert(
            0, case((or_(*(LifeGrowth.text.icontains(term, autoescape=True) for term in terms)), 0), else_=1)
        )
    if preferred_growth is not None:
        ranking.insert(0, case((LifeGrowth.id == preferred_growth, 0), else_=1))
    ranked = (
        select(
            LifeGrowth.id,
            func.row_number().over(partition_by=LifeGrowth.aspect, order_by=ranking).label("position"),
        )
        .where(LifeGrowth.user_id == user_id)
        .subquery()
    )
    growth = await db.scalars(
        select(LifeGrowth)
        .join(ranked, ranked.c.id == LifeGrowth.id)
        .where(ranked.c.position <= 6)
        .order_by(ranked.c.position, LifeGrowth.id)
    )
    recent = [event_view(row) for row in events]
    shared_completed = {
        item.pursuit_id
        for item in recent
        if item.shared_with_user and item.kind == "life.pursuit.completed" and item.pursuit_id is not None
    }
    return LifeContext(
        state=state,
        shared_completion_results=await visible_completion_results(db, user_id, shared_completed),
        private_works=await completed_personal_works(db, user_id, preferred_events=preferred_events, terms=terms)
        if not publication_events_only
        else [],
        personal_growth=[PersonalGrowth.model_validate(row) for row in growth],
        active_pursuits=[pursuit_view(row) for row in pursuit_rows if row.status == "active"][:pursuit_limit],
        paused_pursuits=[pursuit_view(row) for row in pursuit_rows if row.status == "paused"][:pursuit_limit],
        recent_events=recent,
        topic_heads=await trajectory_topic_heads(db, user_id, preferred_events=preferred_events)
        if include_topic_heads
        else [],
    )


def _contact_excerpt(event: LifeEvent) -> dict[str, str]:
    return {
        "id": str(event.id),
        "title": event.fact_json["title"],
        "excerpt": event.narrative or "",
        "fact_type": event.fact_type,
        "occurred_at": event.occurred_at.isoformat(),
    }


def _chat_excerpts(user_id: int) -> Select[tuple[LifeEvent]]:
    return select(LifeEvent).where(
        LifeEvent.user_id == user_id,
        LifeEvent.kind == "life.share.prepared",
        LifeEvent.fact_json["channel"].as_string() == "chat",
    )


async def read_contact_excerpt(db: AsyncSession, user_id: int, event_id: int) -> dict[str, str] | None:
    excerpt = await db.scalar(
        _chat_excerpts(user_id).where(LifeEvent.fact_json["event_id"].as_string() == str(event_id)).limit(1)
    )
    return _contact_excerpt(excerpt) if excerpt is not None else None


async def contact_excerpt_sources(db: AsyncSession, user_id: int, ids: Collection[int]) -> dict[int, dict[str, str]]:
    if not ids:
        return {}
    return {
        row.id: _contact_excerpt(row) for row in await db.scalars(_chat_excerpts(user_id).where(LifeEvent.id.in_(ids)))
    }


async def read_diary_excerpts(
    db: AsyncSession, user_id: int, *, since: datetime, until: datetime
) -> list[dict[str, str | None]]:
    """按挑选日期读取供日记使用的片段，保留经历原本的发生时间。"""
    query = (
        select(LifeEvent)
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.kind == "life.share.prepared",
            LifeEvent.fact_json["channel"].as_string() == "diary",
            LifeEvent.created_at >= since,
            LifeEvent.created_at < until,
        )
        .order_by(LifeEvent.created_at.desc(), LifeEvent.id.desc())
    )
    excerpts = (await db.scalars(query)).all()
    if not excerpts:
        return []
    timezone = await resolve_user_timezone(db, user_id)
    return [
        {
            "id": str(row.id),
            "source_id": row.fact_json["event_id"],
            "title": row.fact_json.get("title"),
            "narrative": row.narrative,
            "fact_type": row.fact_type,
            "occurred_at": format_local_iso(row.occurred_at, timezone),
            "selected_at": format_local_iso(row.created_at, timezone),
        }
        for row in excerpts
    ]
