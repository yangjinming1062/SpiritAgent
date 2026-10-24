"""生活状态的原子读写与幂等事实接纳。"""

import json
from datetime import datetime

from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifePursuit, LifeState, LifeStep
from .ports import project_life_event
from .schemas import (
    EVIDENCE_EVENT_KINDS,
    TERMINAL_STEP_STATES,
    UNRESOLVED_STEP_STATES,
    LifeDecisionRequest,
    LifeEventInput,
)


class LifeStateConflictError(ValueError):
    pass


class LifeInputError(ValueError):
    pass


logger = get_logger(__name__)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def _project_event(
    db: AsyncSession,
    user_id: int,
    event: LifeEventInput,
    occurred_at: datetime,
) -> None:
    if not event.is_meaningful:
        return
    try:
        async with db.begin_nested():
            await project_life_event(db, user_id, event, occurred_at)
    except Exception:
        # 事件事实优先保存；记忆是可重建的派生投影，失败不能回滚生活状态或业务结果。
        logger.warning(
            "life event memory projection failed",
            extra={"user_id": user_id, "event_key": event.event_key},
            exc_info=True,
        )


def _event_matches(row: LifeEvent, event: LifeEventInput) -> bool:
    return (
        row.kind == event.kind
        and row.source == event.source
        and row.subject == event.subject
        and row.fact_json == _dump(event.fact)
        and row.pursuit_id == event.pursuit_id
        and row.step_id == event.step_id
        and row.result_ref_json == _dump(event.result_ref)
        and row.is_meaningful == event.is_meaningful
    )


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


def apply_decision(row: LifeState, decision: LifeDecisionRequest) -> None:
    if row.revision != decision.expected_revision:
        raise LifeStateConflictError("生活状态已在其他流程更新，请重新读取后重试")
    if decision.operation != "keep":
        if decision.current_activity is not None:
            row.current_activity = decision.current_activity
        if decision.current_scene is not None and decision.current_scene != json.loads(row.current_scene_json or "{}"):
            row.current_scene_json = _dump(decision.current_scene)
            row.scene_revision += 1
        if decision.mood is not None:
            row.mood = decision.mood
    if decision.next_review_at is not None:
        row.next_review_at = decision.next_review_at
    row.revision += 1


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
    state = await get_or_create_state(db, user_id, lock=True)
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
    try:
        async with db.begin_nested():
            db.add(row)
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
    if state.active_pursuit_id is None:
        state.active_pursuit_id = row.id
        state.revision += 1
    return row


async def _evidence_events(
    db: AsyncSession,
    user_id: int,
    pursuit: LifePursuit,
    event_ids: set[int],
) -> list[LifeEvent]:
    if not event_ids:
        return []
    events = list(await db.scalars(select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.id.in_(event_ids))))
    if len(events) != len(event_ids):
        raise LifeInputError("生活结果证据不存在或不属于当前账户")
    for event in events:
        if (
            event.subject != "companion"
            or not event.is_meaningful
            or event.pursuit_id not in (None, pursuit.id)
            or event.kind not in EVIDENCE_EVENT_KINDS
            or not json.loads(event.result_ref_json or "{}")
        ):
            raise LifeInputError("事项证据必须是实际完成的步骤或已发布结果")
    step_ids = {event.step_id for event in events if event.kind == "life.step.completed"}
    if step_ids and step_ids != set(
        await db.scalars(
            select(LifeStep.id).where(
                LifeStep.user_id == user_id,
                LifeStep.id.in_(step_ids),
                LifeStep.pursuit_id == pursuit.id,
                LifeStep.state == "completed",
            )
        )
    ):
        raise LifeInputError("步骤尚未完成或不属于当前事项")
    return events


async def _has_unresolved_steps(db: AsyncSession, user_id: int, pursuit_id: int) -> bool:
    return (
        await db.scalar(
            select(LifeStep.id)
            .where(
                LifeStep.user_id == user_id,
                LifeStep.pursuit_id == pursuit_id,
                LifeStep.state.in_(UNRESOLVED_STEP_STATES),
            )
            .limit(1)
        )
        is not None
    )


async def _latest_active_pursuit_id(db: AsyncSession, user_id: int, *, exclude: int) -> int | None:
    return await db.scalar(
        select(LifePursuit.id)
        .where(LifePursuit.user_id == user_id, LifePursuit.status == "active", LifePursuit.id != exclude)
        .order_by(LifePursuit.updated_at.desc(), LifePursuit.id.desc())
        .limit(1)
    )


async def advance_pursuit(db: AsyncSession, user_id: int, decision: LifeDecisionRequest) -> LifePursuit:
    state = await get_or_create_state(db, user_id, lock=True)
    pursuit = await db.scalar(
        select(LifePursuit)
        .where(
            LifePursuit.user_id == user_id,
            LifePursuit.id == decision.pursuit_id,
        )
        .with_for_update()
    )
    if pursuit is None:
        raise LifeInputError("持续事项不存在或不属于当前账户")
    completing = decision.operation == "complete_pursuit"
    if completing and pursuit.status == "completed":
        return pursuit
    if pursuit.status != "active":
        raise LifeInputError("只能推进进行中的持续事项")
    evidence_ids = set(decision.evidence_event_ids)
    evidence = await _evidence_events(db, user_id, pursuit, evidence_ids)
    if decision.progress is not None and decision.progress != pursuit.progress and not evidence:
        raise LifeInputError("更新完成进度需要真实结果证据")
    if completing:
        if not evidence or not (decision.progress or "").strip():
            raise LifeInputError("完成事项需要真实结果证据和完成总结")
        if await _has_unresolved_steps(db, user_id, pursuit.id):
            raise LifeInputError("事项仍有未完成或结果未知的步骤")
    if decision.progress is not None:
        pursuit.progress = decision.progress
    if decision.next_step is not None:
        pursuit.next_step = decision.next_step
    if completing:
        pursuit.status = "completed"
        pursuit.completed_at = utc_now()
        pursuit.next_step = ""
        pursuit.result_ref_json = _dump(
            {
                "event_ids": sorted(evidence_ids),
                "results": [json.loads(event.result_ref_json) for event in evidence],
            }
        )
        if state.active_pursuit_id == pursuit.id:
            state.active_pursuit_id = await _latest_active_pursuit_id(db, user_id, exclude=pursuit.id)
    else:
        state.active_pursuit_id = pursuit.id
    await db.flush()
    await record_event(
        db,
        user_id,
        LifeEventInput(
            event_key=f"pursuit:{pursuit.id}:completed"
            if completing
            else f"pursuit:{pursuit.id}:revision:{state.revision}:progressed",
            kind="life.pursuit.completed" if completing else "life.pursuit.progressed",
            source="life_decision",
            pursuit_id=pursuit.id,
            fact={"title": pursuit.title, "progress": pursuit.progress, "next_step": pursuit.next_step},
            result_ref=json.loads(pursuit.result_ref_json) if completing else {"event_ids": sorted(evidence_ids)},
            is_meaningful=bool(evidence),
        ),
    )
    return pursuit


async def create_step(
    db: AsyncSession,
    user_id: int,
    *,
    idempotency_key: str,
    kind: str,
    description: str,
    pursuit_id: int | None = None,
) -> LifeStep:
    state = await get_or_create_state(db, user_id, lock=True)
    if not idempotency_key.strip():
        raise LifeInputError("生活步骤必须有幂等键")
    existing = await db.scalar(
        select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    if (
        pursuit_id is not None
        and await db.scalar(
            select(LifePursuit.id).where(
                LifePursuit.user_id == user_id,
                LifePursuit.id == pursuit_id,
                LifePursuit.status == "active",
            )
        )
        is None
    ):
        raise LifeInputError("持续事项不存在、不属于当前账户或已结束")
    row = LifeStep(
        user_id=user_id,
        pursuit_id=pursuit_id,
        idempotency_key=idempotency_key,
        kind=kind,
        description=description,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.idempotency_key == idempotency_key),
        )
        if existing is not None:
            return existing
        raise
    state.revision += 1
    await db.flush()
    return row


async def record_event(db: AsyncSession, user_id: int, event: LifeEventInput) -> LifeEvent:
    occurred_at = event.occurred_at or utc_now()
    existing = await db.scalar(
        select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.event_key == event.event_key)
    )
    if existing is not None:
        if _event_matches(existing, event):
            await _project_event(db, user_id, event, existing.occurred_at)
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
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.event_key == event.event_key),
        )
        if existing is not None:
            if _event_matches(existing, event):
                await _project_event(db, user_id, event, existing.occurred_at)
            return existing
        raise
    await _project_event(db, user_id, event, occurred_at)
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
    progress: str | None = None,
    next_step: str | None = None,
) -> LifeStep:
    life_state = await get_or_create_state(db, user_id, lock=True)
    if state not in TERMINAL_STEP_STATES:
        raise LifeInputError("不支持的步骤终态")
    row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id).with_for_update())
    if row is None:
        raise LifeInputError("生活步骤不存在")
    if row.state in {"completed", "cancelled"} or (row.state in {"failed", "unknown"} and state != "cancelled"):
        return row
    row.state = state
    row.result_ref_json = _dump(result_ref or {})
    row.error = error
    row.finished_at = utc_now()
    life_state.revision += 1
    if row.pursuit_id is not None and state in {"completed", "failed", "cancelled"}:
        pursuit = await db.scalar(
            select(LifePursuit)
            .where(LifePursuit.user_id == user_id, LifePursuit.id == row.pursuit_id)
            .with_for_update(),
        )
        if pursuit is not None and pursuit.status == "active":
            if progress is not None:
                pursuit.progress = progress
            elif state == "completed":
                pursuit.progress = row.description
            if next_step is not None:
                pursuit.next_step = next_step
            elif state == "completed":
                pursuit.next_step = ""
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
    life_state = await get_or_create_state(db, user_id, lock=True)
    row = await db.scalar(
        select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id).with_for_update(),
    )
    if row is None:
        raise LifeInputError("生活步骤不存在")
    was_failed = row.state == "failed"
    if row.state in {"completed", "cancelled", "unknown"} or (was_failed and not restart):
        return row
    if row.state == "running" and (business_ref is None or json.loads(row.business_ref_json or "{}") == business_ref):
        return row
    row.state = "running"
    row.started_at = row.started_at or utc_now()
    row.finished_at = None
    row.error = None
    if was_failed:
        row.result_ref_json = "{}"
    if business_ref is not None:
        row.business_ref_json = _dump(business_ref)
    life_state.revision += 1
    await db.flush()
    return row
