"""生活领域的统一只读上下文。

跨业务流程只通过这里取得生活状态、持续事项和近期事实，避免聊天、影像、动态
和日记分别拼出互相漂移的背景。
"""

from datetime import datetime

from infrastructure.text import format_local_iso, utc_now
from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.timezone import resolve_user_timezone

from .models import LifeEvent, LifePursuit
from .queries import empty_state_view, event_view, get_life_state_view, pursuit_view
from .schemas import LifeContext, LifeShareChannel
from .trajectory import trajectory_scope, trajectory_topic_heads


async def read_life_context_in_session(
    db: AsyncSession,
    user_id: int,
    *,
    pursuit_limit: int = 5,
    event_limit: int = 12,
    include_topic_heads: bool = False,
    event_start: datetime | None = None,
    event_end: datetime | None = None,
    publication_events_only: bool = False,
) -> LifeContext:
    """在调用方事务内读取一份一致的生活快照。"""

    state = await get_life_state_view(db, user_id) or empty_state_view(user_id)
    if state.personal_growth:
        evidence_ids = {item.evidence_event_id for item in state.personal_growth.values()}
        current_evidence = set(
            await db.scalars(select(LifeEvent.id).where(LifeEvent.user_id == user_id, LifeEvent.id.in_(evidence_ids)))
        )
        state.personal_growth = {
            aspect: item for aspect, item in state.personal_growth.items() if item.evidence_event_id in current_evidence
        }
    pursuit_rows = []
    for status in ("active", "paused"):
        priority = (
            LifePursuit.id == state.active_pursuit_id
            if status == "active"
            else (LifePursuit.status_source == "companion")
            & or_(LifePursuit.resume_after.is_(None), LifePursuit.resume_after <= utc_now())
        )
        pursuit_rows.extend(
            await db.scalars(
                select(LifePursuit)
                .where(LifePursuit.user_id == user_id, LifePursuit.status == status)
                .order_by(case((priority, 0), else_=1), LifePursuit.updated_at.desc(), LifePursuit.id.desc())
                .limit(max(1, pursuit_limit))
            )
        )
    criteria = [LifeEvent.user_id == user_id, trajectory_scope(shared_only=publication_events_only)]
    if publication_events_only:
        criteria.append(LifeEvent.source != "life_share")
    if event_start is not None:
        criteria.append(LifeEvent.occurred_at >= event_start)
    if event_end is not None:
        criteria.append(LifeEvent.occurred_at < event_end)
    events = await db.scalars(
        select(LifeEvent)
        .where(*criteria)
        .order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
        .limit(event_limit)
    )
    return LifeContext(
        state=state,
        active_pursuits=[pursuit_view(row) for row in pursuit_rows if row.status == "active"][:pursuit_limit],
        paused_pursuits=[pursuit_view(row) for row in pursuit_rows if row.status == "paused"][:pursuit_limit],
        recent_events=[event_view(row) for row in events],
        topic_heads=await trajectory_topic_heads(db, user_id) if include_topic_heads else [],
    )


async def read_life_share_excerpts(
    db: AsyncSession, user_id: int, *, channel: LifeShareChannel, since: datetime, until: datetime
) -> list[dict[str, str | None]]:
    """只把伙伴指定给本渠道的展示片段交给创作模型。"""
    rows = await db.scalars(
        select(LifeEvent)
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.kind == "life.share.prepared",
            LifeEvent.fact_json["channel"].as_string() == channel,
            LifeEvent.created_at >= since,
            LifeEvent.created_at < until,
        )
        .order_by(LifeEvent.created_at.desc(), LifeEvent.id.desc())
        .limit(6)
    )
    excerpts = rows.all()
    if not excerpts:
        return []
    timezone = await resolve_user_timezone(db, user_id)
    return [
        {
            "title": row.fact_json.get("title"),
            "narrative": row.narrative,
            "fact_type": row.fact_type,
            "occurred_at": format_local_iso(row.occurred_at, timezone),
            "selected_at": format_local_iso(row.created_at, timezone),
        }
        for row in excerpts
    ]
