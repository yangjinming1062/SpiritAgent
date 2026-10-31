"""生活状态的原子读写与幂等事实接纳。"""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from infrastructure.api_ids import parse_resource_id
from infrastructure.domain_locks import transaction_owner, transaction_owns
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import inspect, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_dirty, set_committed_value
from sqlalchemy.sql.selectable import CTE

from .models import (
    RESULT_REFERENCE_FIELDS,
    LifeActionAdmission,
    LifeEvent,
    LifePursuit,
    LifeState,
    LifeStep,
    PursuitEvidence,
)
from .ports import LifeEventProjection, project_life_events
from .queries import result_reference
from .schemas import (
    EVIDENCE_EVENT_KINDS,
    UNRESOLVED_STEP_STATES,
    FactType,
    LifeDecisionRequest,
    LifeEventInput,
)


class LifeStateConflictError(ValueError):
    pass


class LifeInputError(ValueError):
    pass


logger = get_logger(__name__)


def result_values(value: dict) -> dict[str, Any]:
    result = deepcopy(value)
    fields = {}
    for key, field in RESULT_REFERENCE_FIELDS.items():
        identifier = result.pop(key, None)
        fields[field] = parse_resource_id(identifier) if identifier is not None else None
    return {**fields, "result_ref_json": result}


def assign_result(row: LifeStep | LifeEvent, value: dict) -> None:
    for key, item in result_values(value).items():
        setattr(row, key, item)


async def _project_events(db: AsyncSession, user_id: int, events: Sequence[LifeEventProjection]) -> None:
    meaningful = [item for item in events if item.event.is_meaningful]
    if not meaningful:
        return
    try:
        async with db.begin_nested():
            await project_life_events(db, user_id, meaningful)
        return
    except Exception:
        logger.warning("life event memory batch projection failed", extra={"user_id": user_id}, exc_info=True)
    if len(meaningful) == 1:
        return
    # 一条投影失败不能阻止其余事实生成索引；异常路径单独隔离重试。
    for item in meaningful:
        try:
            async with db.begin_nested():
                await project_life_events(db, user_id, [item])
        except Exception:
            logger.warning(
                "life event memory projection failed",
                extra={"user_id": user_id, "event_key": item.event.event_key},
                exc_info=True,
            )


def _event_matches(row: LifeEvent, event: LifeEventInput) -> bool:
    return (
        row.kind == event.kind
        and row.source == event.source
        and row.subject == event.subject
        and row.fact_type == event.fact_type
        and row.fact_json == event.fact
        and row.narrative == event.narrative
        and row.pursuit_id == event.pursuit_id
        and row.step_id == event.step_id
        and result_reference(row)
        == {
            key: parse_resource_id(value) if key in RESULT_REFERENCE_FIELDS and value is not None else value
            for key, value in event.result_ref.items()
            if key not in RESULT_REFERENCE_FIELDS or value is not None
        }
        and row.is_meaningful == event.is_meaningful
    )


async def get_or_create_state(db: AsyncSession, user_id: int, *, lock: bool = False) -> LifeState:
    cache = db.info.setdefault("life_state_rows", {})
    cached = cache.get(user_id)
    transaction = transaction_owner(db)
    if cached is not None and transaction_owns(transaction, cached[0]) and (not lock or cached[2]):
        row = cached[1]
        if inspect(row).expired:
            pending = {
                attribute.key: attribute.history.added[0]
                for attribute in inspect(row).attrs
                if attribute.history.has_changes() and attribute.history.added
            }
            await db.refresh(row, with_for_update=cached[2])
            for key, value in pending.items():
                setattr(row, key, value)
        return row
    if lock and any(
        isinstance(row, LifeState)
        and inspect(row).identity == (user_id,)
        and db.is_modified(row, include_collections=False)
        for row in db.dirty
    ):
        raise LifeStateConflictError("生活状态已在取得写锁前修改，请回滚并在写锁内重试")
    if lock:
        # 单条 upsert 同时处理首次建立与并发锁定；冲突不改变业务字段或 revision。
        statement = (
            insert(LifeState)
            .values(user_id=user_id)
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={"user_id": user_id},
            )
            .returning(LifeState)
        )
        row = (await db.scalars(statement.execution_options(populate_existing=True))).one()
    else:
        row = await db.scalar(select(LifeState).where(LifeState.user_id == user_id))
        if row is None:
            row = await db.scalar(
                insert(LifeState)
                .values(user_id=user_id)
                .on_conflict_do_nothing(index_elements=["user_id"])
                .returning(LifeState)
            )
            if row is None:
                row = await db.scalar(select(LifeState).where(LifeState.user_id == user_id))
            if row is None:
                raise LifeStateConflictError("生活状态建立失败，请重试")
    cache[user_id] = (transaction_owner(db), row, lock)
    return row


def apply_decision(row: LifeState, decision: LifeDecisionRequest) -> None:
    if row.revision != decision.expected_revision:
        raise LifeStateConflictError("生活状态已在其他流程更新，请重新读取后重试")
    if decision.operation != "keep":
        activity_changed = (
            decision.current_activity is not None and decision.current_activity != row.current_activity
        ) or (decision.activity_kind is not None and decision.activity_kind != row.activity_kind)
        presentation_changed = activity_changed
        if activity_changed:
            row.activity_started_at = utc_now()
            row.activity_step_id = None
            row.activity_kind = decision.activity_kind or "virtual_activity"
            # 新活动未给出类别时不继承旧活动语义。
            row.activity_type = decision.activity_type or "idle"
        if decision.activity_type is not None and decision.activity_type != row.activity_type:
            row.activity_type = decision.activity_type
            presentation_changed = True
        if decision.current_activity is not None:
            row.current_activity = decision.current_activity
        scene_changed = decision.current_scene is not None and decision.current_scene != deepcopy(
            row.current_scene_json or {}
        )
        if scene_changed:
            row.current_scene_json = deepcopy(decision.current_scene)
            if decision.scene_tags is None:
                row.scene_tags_json = []
            presentation_changed = True
        if decision.scene_tags is not None and decision.scene_tags != row.scene_tags_json:
            row.scene_tags_json = list(decision.scene_tags)
            scene_changed = True
            presentation_changed = True
        if scene_changed:
            row.scene_revision += 1
        if decision.mood is not None and decision.mood != row.mood:
            row.mood = decision.mood
            presentation_changed = True
        if presentation_changed:
            row.presentation_revision += 1
    if decision.next_review_at is not None:
        row.next_review_at = decision.next_review_at
    row.revision += 1


def clear_activity_step(db: AsyncSession, state: LifeState, *, kind: FactType = "intent") -> None:
    """执行结束或退回等待后作废原活动表现，不把已结束的活动继续投影为正在进行。"""
    state.activity_step_id = None
    state.narrative = None
    state.activity_kind = kind
    state.activity_type = "idle"
    state.presentation_revision += 1
    emit_ws_event(
        db,
        user_id=state.user_id,
        event_type="companion.life.presentation_requested",
        payload={"revision": state.presentation_revision},
    )


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
    narrative: str | None = None,
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
        narrative=narrative,
        status_reason=reason,
        status_changed_at=utc_now(),
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
            or event.fact_type != "result"
            or not result_reference(event)
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
    if decision.narrative.pursuit is not None:
        pursuit.narrative = decision.narrative.pursuit
    if completing:
        pursuit.status = "completed"
        pursuit.completed_at = utc_now()
        pursuit.status_changed_at = pursuit.completed_at
        pursuit.status_source = decision.source
        pursuit.status_reason = decision.status_reason.strip() or decision.progress.strip()
        pursuit.next_step = ""
        pursuit.result_ref_json = {
            "evidence_count": len(evidence_ids),
            "results": [deepcopy(result_reference(event)) for event in evidence],
        }
        if state.active_pursuit_id == pursuit.id:
            state.active_pursuit_id = await _latest_active_pursuit_id(db, user_id, exclude=pursuit.id)
    else:
        state.active_pursuit_id = pursuit.id
    if evidence_ids:
        await db.execute(
            insert(PursuitEvidence)
            .values(
                [
                    {"user_id": user_id, "pursuit_id": pursuit.id, "event_id": event_id}
                    for event_id in sorted(evidence_ids)
                ]
            )
            .on_conflict_do_nothing(constraint="uq_pursuit_evidence_event")
        )
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
            fact_type="result" if evidence else "intent",
            pursuit_id=pursuit.id,
            fact={"title": pursuit.title, "progress": pursuit.progress, "next_step": pursuit.next_step},
            narrative=decision.narrative.event or decision.narrative.pursuit,
            result_ref=deepcopy(pursuit.result_ref_json) if completing else {"evidence_count": len(evidence_ids)},
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
    narrative: str | None = None,
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
        narrative=narrative,
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


async def _record_event_batch(
    db: AsyncSession,
    user_id: int,
    events: Sequence[LifeEventInput],
    state: LifeState,
    *,
    state_changes: int = 0,
    changes: Sequence[CTE] = (),
) -> list[LifeEvent]:
    unique: dict[str, LifeEventInput] = {}
    for event in events:
        if event.event_key in unique and unique[event.event_key] != event:
            raise LifeInputError("同批生活事件键不能对应不同事实")
        unique[event.event_key] = event
    inputs = list(unique.values())
    inserted: dict[str, LifeEvent] = {}
    for start in range(0, len(inputs), 500):
        values = [
            {
                "user_id": user_id,
                "event_key": item.event_key,
                "kind": item.kind,
                "source": item.source,
                "subject": item.subject,
                "fact_type": item.fact_type,
                "fact_json": deepcopy(item.fact),
                "narrative": item.narrative,
                "occurred_at": item.occurred_at or utc_now(),
                "pursuit_id": item.pursuit_id,
                "step_id": item.step_id,
                "is_meaningful": item.is_meaningful,
                **result_values(item.result_ref),
            }
            for item in inputs[start : start + 500]
        ]
        rows = await db.scalars(
            insert(LifeEvent)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_life_events_user_key")
            .returning(LifeEvent)
        )
        inserted.update({row.event_key: row for row in rows})
    existing = {}
    if missing := unique.keys() - inserted.keys():
        existing = {
            row.event_key: row
            for row in await db.scalars(
                select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.event_key.in_(missing))
            )
        }
        if existing.keys() != missing:
            raise LifeStateConflictError("生活事件的并发结果已变化，请重试")
    pending = {
        attribute.key: deepcopy(attribute.history.added[0])
        for attribute in inspect(state).attrs
        if attribute.key not in {"user_id", "source_key", "created_at", "updated_at"}
        and attribute.history.has_changes()
        and attribute.history.added
    }
    if inserted or state_changes or changes or pending:
        pending.update(revision=state.revision + len(inserted) + state_changes, updated_at=utc_now())
        statement = (
            update(LifeState)
            .where(LifeState.user_id == user_id)
            .values(**pending)
            .add_cte(*changes)
            .returning(*LifeState.__table__.c)
        )
        saved = (await db.execute(statement.execution_options(synchronize_session=False))).mappings().one()
        for key, value in saved.items():
            set_committed_value(state, key, value)
        flag_dirty(state)
    records = {**existing, **inserted}
    projections = [
        LifeEventProjection(item.model_copy(deep=True), records[item.event_key].occurred_at, records[item.event_key].id)
        for item in inputs
        if item.event_key in inserted or _event_matches(records[item.event_key], item)
    ]
    for row in inserted.values():
        flag_dirty(row)
    await _project_events(db, user_id, projections)
    await db.flush()
    return [records[item.event_key] for item in inputs]


async def record_events(db: AsyncSession, user_id: int, events: Sequence[LifeEventInput]) -> list[LifeEvent]:
    if not events:
        return []
    state = await get_or_create_state(db, user_id, lock=True)
    return await _record_event_batch(db, user_id, events, state)


async def record_event(db: AsyncSession, user_id: int, event: LifeEventInput) -> LifeEvent:
    return (await record_events(db, user_id, [event]))[0]


async def start_step(
    db: AsyncSession,
    user_id: int,
    step_id: int,
    *,
    business_ref: dict | None = None,
    restart: bool = False,
) -> LifeStep:
    life_state = await get_or_create_state(db, user_id, lock=True)
    row = await db.get(LifeStep, step_id)
    if row is None or row.user_id != user_id:
        raise LifeInputError("生活步骤不存在")
    was_failed = row.state == "failed"
    if row.state in {"completed", "cancelled", "unknown"} or (was_failed and not restart):
        return row
    if row.state == "running" and (
        business_ref is None or {"job_id": row.job_id, "generation_id": row.generation_id} == business_ref
    ):
        return row
    row.state = "running"
    row.disposition, row.wait_reason = "executing", ""
    row.started_at = row.started_at or utc_now()
    row.finished_at = None
    row.error = None
    row.failure_code = None
    if was_failed:
        assign_result(row, {})
    if business_ref is not None:
        await db.execute(
            update(LifeActionAdmission)
            .where(LifeActionAdmission.user_id == user_id, LifeActionAdmission.request_id == row.idempotency_key)
            .values(submitted=True)
        )
        if business_ref.get("action_id") is not None:
            row.business_action_id = parse_resource_id(business_ref["action_id"])
        job_id = business_ref.get("job_id")
        row.job_id = parse_resource_id(job_id) if job_id is not None else None
        row.generation_id = business_ref.get("generation_id")
    life_state.revision += 1
    await db.flush()
    return row
