"""只读查询；调用方不直接读取 Life ORM。"""

import json
from datetime import datetime

from infrastructure.text import utc_now
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifePursuit, LifeState, LifeStep
from .schemas import (
    LifeEventListResponse,
    LifeEventView,
    LifePursuitListResponse,
    LifePursuitView,
    LifeStateView,
    LifeStepView,
)


def _json(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def state_view(row: LifeState) -> LifeStateView:
    return LifeStateView(
        user_id=row.user_id,
        current_activity=row.current_activity,
        current_scene=_json(row.current_scene_json),
        mood=row.mood,
        active_pursuit_id=row.active_pursuit_id,
        next_review_at=row.next_review_at,
        revision=row.revision,
        scene_revision=row.scene_revision,
        updated_at=row.updated_at,
    )


def empty_state_view(user_id: int) -> LifeStateView:
    """返回尚未落库账户的初始生活状态。"""

    return LifeStateView(
        user_id=user_id,
        current_activity="",
        current_scene={},
        mood=None,
        active_pursuit_id=None,
        next_review_at=None,
        revision=0,
        scene_revision=0,
        updated_at=utc_now(),
    )


def pursuit_view(row: LifePursuit) -> LifePursuitView:
    return LifePursuitView(
        id=row.id,
        title=row.title,
        reason=row.reason,
        desired_result=row.desired_result,
        progress=row.progress,
        next_step=row.next_step,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        result_ref=_json(row.result_ref_json),
        updated_at=row.updated_at,
    )


def step_view(row: LifeStep) -> LifeStepView:
    return LifeStepView(
        id=row.id,
        pursuit_id=row.pursuit_id,
        idempotency_key=row.idempotency_key,
        kind=row.kind,
        description=row.description,
        state=row.state,
        business_ref=_json(row.business_ref_json),
        result_ref=_json(row.result_ref_json),
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def event_view(row: LifeEvent) -> LifeEventView:
    return LifeEventView(
        id=row.id,
        event_key=row.event_key,
        kind=row.kind,
        source=row.source,
        subject=row.subject,
        fact=_json(row.fact_json),
        occurred_at=row.occurred_at,
        pursuit_id=row.pursuit_id,
        step_id=row.step_id,
        result_ref=_json(row.result_ref_json),
        is_meaningful=row.is_meaningful,
    )


async def get_life_state(db: AsyncSession, user_id: int) -> LifeState | None:
    return await db.scalar(select(LifeState).where(LifeState.user_id == user_id))


async def get_life_mood(db: AsyncSession, user_id: int) -> str | None:
    """读取当前心情，避免调用方依赖 Life ORM。"""
    return await db.scalar(select(LifeState.mood).where(LifeState.user_id == user_id))


async def get_life_state_view(db: AsyncSession, user_id: int) -> LifeStateView | None:
    row = await get_life_state(db, user_id)
    return state_view(row) if row else None


async def get_pursuit(db: AsyncSession, user_id: int, pursuit_id: int) -> LifePursuit | None:
    return await db.scalar(select(LifePursuit).where(LifePursuit.user_id == user_id, LifePursuit.id == pursuit_id))


async def list_pursuits(
    db: AsyncSession,
    user_id: int,
    *,
    status: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> LifePursuitListResponse:
    where = [LifePursuit.user_id == user_id]
    if status:
        where.append(LifePursuit.status == status)
    total = int(await db.scalar(select(func.count()).select_from(LifePursuit).where(*where)) or 0)
    rows = list(
        await db.scalars(
            select(LifePursuit)
            .where(*where)
            .order_by(LifePursuit.updated_at.desc(), LifePursuit.id.desc())
            .offset(offset)
            .limit(limit),
        ),
    )
    return LifePursuitListResponse(items=[pursuit_view(row) for row in rows], total=total)


async def list_events(
    db: AsyncSession,
    user_id: int,
    *,
    pursuit_id: int | None = None,
    before: datetime | None = None,
    offset: int = 0,
    limit: int = 20,
) -> LifeEventListResponse:
    where = [LifeEvent.user_id == user_id]
    if pursuit_id is not None:
        where.append(LifeEvent.pursuit_id == pursuit_id)
    if before is not None:
        where.append(LifeEvent.occurred_at < before)
    total = int(await db.scalar(select(func.count()).select_from(LifeEvent).where(*where)) or 0)
    rows = list(
        await db.scalars(
            select(LifeEvent)
            .where(*where)
            .order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
            .offset(offset)
            .limit(limit),
        ),
    )
    return LifeEventListResponse(items=[event_view(row) for row in rows], total=total)
