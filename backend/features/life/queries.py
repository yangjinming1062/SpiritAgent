"""只读查询；调用方不直接读取 Life ORM。"""

from collections.abc import Collection
from datetime import datetime

from infrastructure.pagination import decode_timestamp_cursor, encode_timestamp_cursor
from infrastructure.text import utc_now
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from .actions import ReadingResult
from .models import RESULT_REFERENCE_FIELDS, LifeEvent, LifePursuit, LifeState, LifeStep
from .schemas import (
    EVIDENCE_EVENT_KINDS,
    EVIDENCE_EVENTS_PER_PURSUIT,
    UNRESOLVED_STEP_STATES,
    LifeEventListResponse,
    LifeEventView,
    LifePursuitEvidence,
    LifePursuitListResponse,
    LifePursuitView,
    LifeStateView,
    LifeStepView,
)


def state_view(row: LifeState) -> LifeStateView:
    return LifeStateView(
        user_id=row.user_id,
        current_activity=row.current_activity,
        activity_kind=row.activity_kind,
        activity_started_at=row.activity_started_at,
        activity_step_id=row.activity_step_id,
        presentation_revision=row.presentation_revision,
        current_scene=row.current_scene_json,
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


def result_reference(row: LifeStep | LifeEvent) -> dict:
    result = dict(row.result_ref_json)
    for key, field in RESULT_REFERENCE_FIELDS.items():
        value = getattr(row, field)
        if value is not None:
            result[key] = value
    return result


def pursuit_view(row: LifePursuit) -> LifePursuitView:
    return LifePursuitView(
        id=row.id,
        title=row.title,
        reason=row.reason,
        desired_result=row.desired_result,
        progress=row.progress,
        next_step=row.next_step,
        status=row.status,
        status_reason=row.status_reason,
        status_source=row.status_source,
        status_changed_at=row.status_changed_at,
        resume_after=row.resume_after,
        previous_pursuit_id=row.previous_pursuit_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        result_ref=row.result_ref_json,
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
        source=row.source,
        action=row.action_json,
        disposition=row.disposition,
        wait_reason=row.wait_reason,
        not_before=row.not_before,
        reading_result=ReadingResult.model_validate(row.reading_result_json).model_dump(
            exclude={"sources": {"__all__": {"content"}}}
        )
        if row.reading_result_json
        else None,
        business_ref={
            **({"job_id": row.job_id, "generation_id": row.generation_id} if row.job_id is not None else {}),
            **({"action_id": row.business_action_id} if row.business_action_id is not None else {}),
        },
        result_ref=result_reference(row),
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
        fact_type=row.fact_type,
        fact=row.fact_json,
        occurred_at=row.occurred_at,
        pursuit_id=row.pursuit_id,
        step_id=row.step_id,
        result_ref=result_reference(row),
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


async def due_review_user_ids(db: AsyncSession, user_ids: Collection[int], now: datetime) -> set[int]:
    """缺少生活状态的就绪账户也需要首次判断。"""
    if not user_ids:
        return set()
    waiting = set(
        await db.scalars(
            select(LifeState.user_id).where(
                LifeState.user_id.in_(user_ids),
                LifeState.next_review_at > now,
            )
        )
    )
    return set(user_ids) - waiting


async def pursuit_evidence(
    db: AsyncSession,
    user_id: int,
    pursuit_ids: Collection[int],
) -> LifePursuitEvidence:
    """按事项补充未解决步骤与可引用的结果依据。"""
    if not pursuit_ids:
        return LifePursuitEvidence()
    steps = await db.scalars(
        select(LifeStep)
        .where(
            LifeStep.user_id == user_id,
            LifeStep.pursuit_id.in_(pursuit_ids),
            LifeStep.state.in_(UNRESOLVED_STEP_STATES),
        )
        .order_by(LifeStep.pursuit_id, LifeStep.id)
    )
    ranked = (
        select(
            LifeEvent.id,
            func.row_number()
            .over(partition_by=LifeEvent.pursuit_id, order_by=(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()))
            .label("position"),
        )
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.pursuit_id.in_(pursuit_ids),
            LifeEvent.subject == "companion",
            LifeEvent.is_meaningful.is_(True),
            LifeEvent.kind.in_(EVIDENCE_EVENT_KINDS),
            LifeEvent.fact_type == "result",
            or_(
                LifeEvent.result_ref_json != {},
                *(getattr(LifeEvent, field).is_not(None) for field in RESULT_REFERENCE_FIELDS.values()),
            ),
        )
        .subquery()
    )
    events = await db.scalars(
        select(LifeEvent)
        .join(ranked, LifeEvent.id == ranked.c.id)
        .where(ranked.c.position <= EVIDENCE_EVENTS_PER_PURSUIT)
        .order_by(LifeEvent.pursuit_id, LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
    )
    return LifePursuitEvidence(steps=[step_view(row) for row in steps], events=[event_view(row) for row in events])


async def list_pursuits(
    db: AsyncSession,
    user_id: int,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
    include_total: bool = False,
) -> LifePursuitListResponse:
    where = [LifePursuit.user_id == user_id]
    if status:
        where.append(LifePursuit.status == status)
    total = (
        int(await db.scalar(select(func.count()).select_from(LifePursuit).where(*where)) or 0)
        if include_total
        else None
    )
    if before := decode_timestamp_cursor(cursor):
        where.append(tuple_(LifePursuit.updated_at, LifePursuit.id) < before)
    rows = list(
        await db.scalars(
            select(LifePursuit)
            .where(*where)
            .order_by(LifePursuit.updated_at.desc(), LifePursuit.id.desc())
            .limit(limit + 1)
        )
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return LifePursuitListResponse(
        items=[pursuit_view(row) for row in rows],
        total=total,
        has_more=more,
        next_cursor=encode_timestamp_cursor(rows[-1].updated_at, rows[-1].id) if more else None,
    )


async def list_events(
    db: AsyncSession,
    user_id: int,
    *,
    pursuit_id: int | None = None,
    before: datetime | None = None,
    cursor: str | None = None,
    limit: int = 20,
    include_total: bool = False,
) -> LifeEventListResponse:
    where = [LifeEvent.user_id == user_id]
    if pursuit_id is not None:
        where.append(LifeEvent.pursuit_id == pursuit_id)
    if before is not None:
        where.append(LifeEvent.occurred_at < before)
    total = (
        int(await db.scalar(select(func.count()).select_from(LifeEvent).where(*where)) or 0) if include_total else None
    )
    if position := decode_timestamp_cursor(cursor):
        where.append(tuple_(LifeEvent.occurred_at, LifeEvent.id) < position)
    rows = list(
        await db.scalars(
            select(LifeEvent).where(*where).order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()).limit(limit + 1)
        )
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return LifeEventListResponse(
        items=[event_view(row) for row in rows],
        total=total,
        has_more=more,
        next_cursor=encode_timestamp_cursor(rows[-1].occurred_at, rows[-1].id) if more else None,
    )
