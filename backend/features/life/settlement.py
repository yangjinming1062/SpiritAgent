"""同一业务任务的生活步骤与事项批量结算。"""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from infrastructure.api_ids import parse_resource_id
from infrastructure.text import utc_now
from sqlalchemy import BigInteger, and_, case, cast, column, inspect, literal, select, update, values
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_dirty, set_committed_value
from sqlalchemy.sql.selectable import CTE

from .models import LifeActionAdmission, LifeEvent, LifePursuit, LifeStep
from .queries import result_reference
from .schemas import TERMINAL_STEP_STATES, LifeEventInput
from .service import (
    LifeInputError,
    LifeStateConflictError,
    _record_event_batch,
    clear_activity_step,
    get_or_create_state,
    result_values,
)


def _changes_cte(
    model: type[LifeStep] | type[LifePursuit], user_id: int, changes: dict[int, dict[str, Any]], name: str
) -> CTE:
    fields = tuple(next(iter(changes.values())))
    data = values(
        column("row_id", BigInteger()),
        *(column(key, model.__table__.c[key].type) for key in fields),
        name=f"{name}_values",
    ).data(
        [
            (
                identifier,
                *(cast(None, model.__table__.c[key].type) if change[key] is None else change[key] for key in fields),
            )
            for identifier, change in changes.items()
        ]
    )
    return (
        update(model)
        .where(model.user_id == user_id, model.id == data.c.row_id)
        .values(**{key: data.c[key] for key in fields})
        .returning(model.id)
        .cte(name)
    )


async def settle_business_steps(
    db: AsyncSession,
    user_id: int,
    *,
    kind: str,
    business_ref: dict,
    state: str,
    result_ref: dict | None = None,
    error: str | None = None,
    progress: str | None = None,
    next_step: str | None = None,
    additional_events: Sequence[LifeEventInput] = (),
) -> int | None:
    if state != "running" and state not in TERMINAL_STEP_STATES:
        raise LifeInputError("不支持的步骤状态")
    if not any(business_ref.get(key) is not None for key in ("job_id", "step_id", "action_id")):
        raise LifeInputError("业务步骤必须关联真实任务")
    job_id = parse_resource_id(business_ref["job_id"]) if business_ref.get("job_id") is not None else None
    reference = LifeStep.job_id == job_id
    if business_ref.get("step_id") is not None:
        reference = LifeStep.id == parse_resource_id(business_ref["step_id"])
    elif business_ref.get("action_id") is not None:
        reference = LifeStep.business_action_id == parse_resource_id(business_ref["action_id"])
    life_state = await get_or_create_state(db, user_id, lock=True)
    pending = [row for row in db.dirty if isinstance(row, LifeStep | LifePursuit)]
    if pending:
        if any(inspect(row).attrs.user_id.loaded_value != user_id for row in pending):
            raise LifeStateConflictError("不能在当前账户事务提交其他账户的生活变更")
        await db.flush()
    protected = {"completed", "cancelled"} | ({"failed"} if state != "cancelled" else set())
    outcome = case((LifeStep.state.in_(protected), LifeStep.state), else_=literal(state))
    known = (
        select(LifeEvent.id)
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.step_id == LifeStep.id,
            LifeEvent.kind == literal("life.step.") + outcome,
        )
        .correlate(LifeStep)
        .exists()
    )
    query = (
        select(LifeStep, LifePursuit, known.label("known_outcome"))
        .outerjoin(LifePursuit, and_(LifePursuit.id == LifeStep.pursuit_id, LifePursuit.user_id == user_id))
        .where(LifeStep.user_id == user_id, LifeStep.kind == kind, reference)
        .order_by(LifeStep.id)
    )
    rows = list((await db.execute(query.execution_options(populate_existing=True))).all())
    if not rows and not additional_events:
        return None
    now = utc_now()
    step_changes: dict[int, dict[str, Any]] = {}
    pursuit_changes: dict[int, dict[str, Any]] = {}
    events = list(additional_events)
    step_objects = {}
    pursuit_objects = {}
    submitted_requests = set()
    for row, pursuit, known_outcome in rows:
        step_objects[row.id] = row
        if pursuit is not None:
            pursuit_objects[pursuit.id] = pursuit
        if state == "running":
            if (pursuit is not None and pursuit.status != "active") or row.state in {
                "completed",
                "cancelled",
                "unknown",
            }:
                continue
            generation_id = business_ref.get("generation_id", row.generation_id)
            if row.state == "running" and row.job_id == job_id and row.generation_id == generation_id:
                continue
            step_changes[row.id] = {
                "state": "running",
                "started_at": row.started_at or now,
                "finished_at": None,
                "error": None,
                "job_id": job_id,
                "generation_id": generation_id,
                **result_values({} if row.state == "failed" else result_reference(row)),
                "updated_at": now,
            }
            continue
        changed = row.state not in protected
        final_state = state if changed else row.state
        finished_at = now if changed else row.finished_at
        final_result = deepcopy(result_ref or {}) if changed else result_reference(row)
        final_error = error if changed else row.error
        if changed:
            if state in {"completed", "unknown"}:
                submitted_requests.add(row.idempotency_key)
            step_changes[row.id] = {
                "state": state,
                "disposition": "done" if state == "completed" else state,
                "wait_reason": error or "",
                "finished_at": now,
                "error": error,
                **result_values(final_result),
                "updated_at": now,
            }
            if pursuit is not None and pursuit.status == "active" and state in {"completed", "failed", "cancelled"}:
                current = pursuit_changes.get(
                    pursuit.id, {"progress": pursuit.progress, "next_step": pursuit.next_step, "updated_at": now}
                )
                if progress is not None:
                    current["progress"] = progress
                elif state == "completed":
                    current["progress"] = row.description
                if next_step is not None:
                    current["next_step"] = next_step
                elif state == "completed":
                    current["next_step"] = ""
                pursuit_changes[pursuit.id] = current
        if finished_at is None or known_outcome:
            continue
        events.append(
            LifeEventInput(
                event_key=f"step:{row.id}:{final_state}",
                kind=f"life.step.{final_state}",
                source="life_step",
                fact_type="result" if final_state == "completed" else "execution",
                step_id=row.id,
                pursuit_id=row.pursuit_id,
                fact={"description": row.description, "state": final_state, "error": final_error or ""},
                result_ref=final_result,
            )
        )
        if pursuit is not None and pursuit.status == "active" and final_state == "completed":
            current = pursuit_changes.get(pursuit.id, {"progress": pursuit.progress, "next_step": pursuit.next_step})
            events.append(
                LifeEventInput(
                    event_key=f"pursuit:{pursuit.id}:step:{row.id}:progressed",
                    kind="life.pursuit.progressed",
                    source="life_step",
                    fact_type="result",
                    pursuit_id=pursuit.id,
                    step_id=row.id,
                    fact={"title": pursuit.title, "progress": current["progress"], "next_step": current["next_step"]},
                    result_ref=deepcopy(final_result),
                )
            )
    if submitted_requests:
        await db.execute(
            update(LifeActionAdmission)
            .where(LifeActionAdmission.user_id == user_id, LifeActionAdmission.request_id.in_(submitted_requests))
            .values(submitted=True)
        )
    # 大批次分块，避免 PostgreSQL 参数上限；常见任务在一条数据修改 CTE 中完成。
    changes = []
    for model, updates, label in (
        (LifeStep, step_changes, "settled_steps"),
        (LifePursuit, pursuit_changes, "settled_pursuits"),
    ):
        items = list(updates.items())
        for start in range(0, len(items), 400):
            chunk = dict(items[start : start + 400])
            change = _changes_cte(model, user_id, chunk, f"{label}_{start}")
            if len(step_changes) + len(pursuit_changes) > 400:
                await db.execute(select(literal(1)).add_cte(change))
            else:
                changes.append(change)
    if step_changes:
        life_state.next_review_at = now
        if life_state.activity_step_id in step_changes:
            clear_activity_step(db, life_state, kind="result" if state == "completed" else "intent")
    await _record_event_batch(db, user_id, events, life_state, state_changes=len(step_changes), changes=changes)
    for objects, updates in ((step_objects, step_changes), (pursuit_objects, pursuit_changes)):
        for identifier, change in updates.items():
            row = objects[identifier]
            for field, value in change.items():
                set_committed_value(row, field, value)
            # Core CTE 不重复发 UPDATE，但 SAVEPOINT 回滚仍需让 ORM 视图失效。
            flag_dirty(row)
    return life_state.revision
