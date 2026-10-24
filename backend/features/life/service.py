"""生活状态的原子读写与幂等事实接纳。"""

import json

from infrastructure.text import utc_now
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifePursuit, LifeState, LifeStep
from .schemas import LifeDecisionRequest, LifeEventInput


class LifeStateConflictError(ValueError):
    pass


class LifeInputError(ValueError):
    pass


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def get_or_create_state(db: AsyncSession, user_id: int, *, lock: bool = False) -> LifeState:
    query = select(LifeState).where(LifeState.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is not None:
        return row
    row = LifeState(user_id=user_id)
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        row = await db.scalar(
            select(LifeState).where(LifeState.user_id == user_id).with_for_update()
            if lock
            else select(LifeState).where(LifeState.user_id == user_id)
        )
        if row is None:
            raise
    return row


async def apply_decision(db: AsyncSession, user_id: int, decision: LifeDecisionRequest) -> LifeState:
    row = await get_or_create_state(db, user_id, lock=True)
    if row.revision != decision.expected_revision:
        raise LifeStateConflictError("生活状态已在其他流程更新，请重新读取后重试")
    scene_changed = decision.scene_changed or decision.current_scene is not None
    if decision.current_activity is not None:
        row.current_activity = decision.current_activity
    if decision.current_scene is not None:
        row.current_scene_json = _dump(decision.current_scene)
    if decision.mood is not None:
        row.mood = decision.mood
    if decision.next_review_at is not None:
        row.next_review_at = decision.next_review_at
    row.revision += 1
    if scene_changed:
        row.scene_revision += 1
    await db.flush()
    return row


async def set_mood(db: AsyncSession, user_id: int, mood: str) -> None:
    row = await get_or_create_state(db, user_id, lock=True)
    row.mood = mood
    row.revision += 1
    await db.flush()


async def ensure_pursuit(
    db: AsyncSession,
    user_id: int,
    *,
    title: str,
    reason: str = "",
    desired_result: str = "",
    idempotency_key: str | None = None,
) -> LifePursuit:
    if not title.strip():
        raise LifeInputError("持续事项标题不能为空")
    key = idempotency_key.strip() if idempotency_key else None
    if key:
        existing = await db.scalar(
            select(LifePursuit).where(
                LifePursuit.user_id == user_id,
                LifePursuit.idempotency_key == key,
            ),
        )
        if existing is not None:
            return existing
    row = LifePursuit(
        user_id=user_id,
        idempotency_key=key,
        title=title.strip(),
        reason=reason,
        desired_result=desired_result,
    )
    db.add(row)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        if not key:
            raise
        existing = await db.scalar(
            select(LifePursuit).where(LifePursuit.user_id == user_id, LifePursuit.idempotency_key == key),
        )
        if existing is not None:
            return existing
        raise
    state = await get_or_create_state(db, user_id, lock=True)
    if state.active_pursuit_id is None:
        state.active_pursuit_id = row.id
        state.revision += 1
    return row


async def create_step(
    db: AsyncSession,
    user_id: int,
    *,
    idempotency_key: str,
    kind: str,
    description: str,
    pursuit_id: int | None = None,
) -> LifeStep:
    if not idempotency_key.strip():
        raise LifeInputError("生活步骤必须有幂等键")
    existing = await db.scalar(
        select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    if (
        pursuit_id is not None
        and await db.scalar(select(LifePursuit.id).where(LifePursuit.user_id == user_id, LifePursuit.id == pursuit_id))
        is None
    ):
        raise LifeInputError("持续事项不属于当前账户")
    row = LifeStep(
        user_id=user_id,
        pursuit_id=pursuit_id,
        idempotency_key=idempotency_key,
        kind=kind,
        description=description,
    )
    db.add(row)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.idempotency_key == idempotency_key),
        )
        if existing is not None:
            return existing
        raise
    return row


async def record_event(db: AsyncSession, user_id: int, event: LifeEventInput) -> LifeEvent:
    occurred_at = event.occurred_at or utc_now()
    existing = await db.scalar(
        select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.event_key == event.event_key)
    )
    if existing is not None:
        return existing
    if (
        event.pursuit_id is not None
        and await db.scalar(
            select(LifePursuit.id).where(LifePursuit.user_id == user_id, LifePursuit.id == event.pursuit_id)
        )
        is None
    ):
        raise LifeInputError("持续事项不属于当前账户")
    if (
        event.step_id is not None
        and await db.scalar(select(LifeStep.id).where(LifeStep.user_id == user_id, LifeStep.id == event.step_id))
        is None
    ):
        raise LifeInputError("生活步骤不属于当前账户")
    state = await get_or_create_state(db, user_id, lock=True)
    row = LifeEvent(
        user_id=user_id,
        event_key=event.event_key,
        kind=event.kind,
        source=event.source,
        subject=event.subject,
        fact_json=_dump(event.fact),
        occurred_at=occurred_at,
        pursuit_id=event.pursuit_id,
        step_id=event.step_id,
        result_ref_json=_dump(event.result_ref),
        is_meaningful=event.is_meaningful,
    )
    db.add(row)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.event_key == event.event_key),
        )
        if existing is not None:
            return existing
        raise
    state.revision += 1
    await db.flush()
    return row


async def finish_step(
    db: AsyncSession,
    user_id: int,
    step_id: int,
    *,
    state: str,
    result_ref: dict | None = None,
    error: str | None = None,
) -> LifeStep:
    row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id).with_for_update())
    if row is None:
        raise LifeInputError("生活步骤不存在")
    if row.state in {"completed", "failed", "cancelled", "unknown"}:
        return row
    row.state = state
    row.result_ref_json = _dump(result_ref or {})
    row.error = error
    row.finished_at = utc_now()
    if row.pursuit_id is not None and state in {"completed", "failed", "cancelled"}:
        pursuit = await db.scalar(
            select(LifePursuit)
            .where(LifePursuit.user_id == user_id, LifePursuit.id == row.pursuit_id)
            .with_for_update(),
        )
        if pursuit is not None and state == "completed":
            pursuit.progress = json.dumps(result_ref or {}, ensure_ascii=False)
            pursuit.next_step = ""
            pursuit.status = "completed"
            pursuit.completed_at = row.finished_at
            life_state = await get_or_create_state(db, user_id, lock=True)
            if life_state.active_pursuit_id == pursuit.id:
                life_state.active_pursuit_id = None
                life_state.revision += 1
    await db.flush()
    return row


async def start_step(
    db: AsyncSession,
    user_id: int,
    step_id: int,
    *,
    business_ref: dict | None = None,
    restart: bool = False,
) -> LifeStep:
    row = await db.scalar(
        select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id).with_for_update(),
    )
    if row is None:
        raise LifeInputError("生活步骤不存在")
    was_failed = row.state == "failed"
    if row.state in {"completed", "cancelled", "unknown"} or (was_failed and not restart):
        return row
    row.state = "running"
    row.started_at = row.started_at or utc_now()
    row.finished_at = None
    row.error = None
    if was_failed:
        row.result_ref_json = "{}"
    if business_ref is not None:
        row.business_ref_json = _dump(business_ref)
    await db.flush()
    return row
