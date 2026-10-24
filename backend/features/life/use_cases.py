"""生活状态的流程入口，负责会话边界与提交。"""

import json

from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event
from sqlalchemy import and_, cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifeStep
from .queries import (
    empty_state_view,
    event_view,
    get_life_state_view,
    get_pursuit,
    list_events,
    list_pursuits,
    pursuit_view,
    state_view,
    step_view,
)
from .schemas import (
    LifeDecisionRequest,
    LifeEventInput,
    LifeEventListResponse,
    LifeEventView,
    LifePursuitListResponse,
    LifePursuitView,
    LifeStateView,
    LifeStepRequest,
    LifeStepView,
)
from .service import (
    LifeInputError,
    advance_pursuit,
    apply_decision,
    create_step,
    ensure_pursuit,
    finish_step,
    get_or_create_state,
    record_event,
    start_step,
)


async def read_state(user_id: int) -> LifeStateView:
    async with SESSION_LOCAL() as db:
        view = await get_life_state_view(db, user_id)
        if view is not None:
            return view
    # 读取接口保持只读；缺少持久行表示初始状态，只有流程接纳变更时才落库。
    return empty_state_view(user_id)


async def read_pursuit(user_id: int, pursuit_id: int) -> LifePursuitView | None:
    async with SESSION_LOCAL() as db:
        row = await get_pursuit(db, user_id, pursuit_id)
        return pursuit_view(row) if row else None


async def read_pursuits(user_id: int, *, status: str | None, offset: int, limit: int) -> LifePursuitListResponse:
    async with SESSION_LOCAL() as db:
        return await list_pursuits(db, user_id, status=status, offset=offset, limit=limit)


async def read_events(user_id: int, *, pursuit_id: int | None, offset: int, limit: int) -> LifeEventListResponse:
    async with SESSION_LOCAL() as db:
        return await list_events(db, user_id, pursuit_id=pursuit_id, offset=offset, limit=limit)


async def commit_decision_in_session(
    db: AsyncSession,
    user_id: int,
    decision: LifeDecisionRequest,
) -> LifeStateView:
    row = await get_or_create_state(db, user_id, lock=True)
    previous = state_view(row)
    apply_decision(row, decision)
    await db.flush()
    activity_changed = previous.current_activity != row.current_activity
    scene_changed = previous.scene_revision != row.scene_revision
    mood_changed = previous.mood != row.mood
    if activity_changed or scene_changed or mood_changed:
        await record_event(
            db,
            user_id,
            LifeEventInput(
                event_key=f"life-state:{user_id}:revision:{row.revision}",
                kind="life.state.changed",
                source="life_decision",
                fact={
                    "current_activity": row.current_activity,
                    "current_scene": json.loads(row.current_scene_json or "{}"),
                    "scene_revision": row.scene_revision,
                    "mood": row.mood,
                },
                result_ref={"revision": row.revision},
                is_meaningful=activity_changed,
            ),
        )
    if decision.operation == "start_pursuit":
        pursuit = await begin_pursuit_in_session(
            db,
            user_id,
            title=decision.pursuit_title,
            reason=decision.pursuit_reason,
            desired_result=decision.pursuit_result,
            idempotency_key=f"life:{decision.expected_revision}:{decision.pursuit_title.strip()}",
        )
        row.active_pursuit_id = pursuit.id
    elif decision.operation in {"advance_pursuit", "complete_pursuit"}:
        await advance_pursuit(db, user_id, decision)
    await db.flush()
    await db.refresh(row)
    emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"revision": row.revision})
    return state_view(row)


async def accept_event_in_session(
    db: AsyncSession,
    user_id: int,
    event: LifeEventInput,
) -> LifeEventView:
    """在调用方事务内接纳事件；只返回公开 DTO。"""
    view = event_view(await record_event(db, user_id, event))
    emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"event_key": view.event_key})
    return view


async def begin_pursuit_in_session(
    db: AsyncSession,
    user_id: int,
    *,
    title: str,
    reason: str = "",
    desired_result: str = "",
    idempotency_key: str | None = None,
) -> LifePursuitView:
    row = await ensure_pursuit(
        db, user_id, title=title, reason=reason, desired_result=desired_result, idempotency_key=idempotency_key
    )
    await record_event(
        db,
        user_id,
        LifeEventInput(
            event_key=f"pursuit:{row.id}:started",
            kind="life.pursuit.started",
            source="life_pursuit",
            pursuit_id=row.id,
            fact={"title": row.title, "reason": row.reason, "desired_result": row.desired_result},
            result_ref={"pursuit_id": row.id},
        ),
    )
    emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"pursuit_id": row.id})
    return pursuit_view(row)


async def read_step_by_key_in_session(db: AsyncSession, user_id: int, key: str) -> LifeStepView | None:
    row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.idempotency_key == key))
    return step_view(row) if row is not None else None


async def bind_step_in_session(
    db: AsyncSession,
    user_id: int,
    request: LifeStepRequest,
    *,
    business_ref: dict,
) -> LifeStepView:
    row = await create_step(db, user_id, **request.model_dump())
    row = await start_step(db, user_id, row.id, business_ref=business_ref)
    emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
    return step_view(row)


async def _record_step_event(
    db: AsyncSession,
    user_id: int,
    row: LifeStep,
) -> None:
    if row.finished_at is None:
        return
    # 恢复会重映射步骤 ID，事件的幂等键仍保留原值。
    if (
        await db.scalar(
            select(LifeEvent.id)
            .where(
                LifeEvent.user_id == user_id,
                LifeEvent.step_id == row.id,
                LifeEvent.kind == f"life.step.{row.state}",
            )
            .limit(1)
        )
        is not None
    ):
        return
    await record_event(
        db,
        user_id,
        LifeEventInput(
            event_key=f"step:{row.id}:{row.state}",
            kind=f"life.step.{row.state}",
            source="life_step",
            step_id=row.id,
            pursuit_id=row.pursuit_id,
            fact={"description": row.description, "state": row.state, "error": row.error or ""},
            result_ref=json.loads(row.result_ref_json or "{}"),
        ),
    )
    if row.pursuit_id is not None and row.state == "completed":
        pursuit = await get_pursuit(db, user_id, row.pursuit_id)
        if pursuit is not None and pursuit.status == "active":
            await record_event(
                db,
                user_id,
                LifeEventInput(
                    event_key=f"pursuit:{pursuit.id}:step:{row.id}:progressed",
                    kind="life.pursuit.progressed",
                    source="life_step",
                    pursuit_id=pursuit.id,
                    step_id=row.id,
                    fact={
                        "title": pursuit.title,
                        "progress": pursuit.progress,
                        "next_step": pursuit.next_step,
                    },
                    result_ref=json.loads(row.result_ref_json or "{}"),
                ),
            )


async def update_business_steps_in_session(
    db: AsyncSession,
    user_id: int,
    *,
    kind: str,
    business_ref: dict[str, int],
    idempotency_key: str | None = None,
    state: str,
    result_ref: dict | None = None,
    error: str | None = None,
    progress: str | None = None,
    next_step: str | None = None,
) -> None:
    """业务结果结算全部关联步骤；旧工作项仍可按原幂等键找到步骤。"""
    if not business_ref:
        raise LifeInputError("业务步骤更新必须指定业务引用")
    matches = and_(
        *(cast(LifeStep.business_ref_json, JSONB)[key].astext == str(value) for key, value in business_ref.items())
    )
    if idempotency_key is not None:
        matches = or_(matches, LifeStep.idempotency_key == idempotency_key)
    query = select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.kind == kind, matches)
    if await db.scalar(query.with_only_columns(LifeStep.id).limit(1)) is None:
        return
    await get_or_create_state(db, user_id, lock=True)
    rows = await db.scalars(query.order_by(LifeStep.id).with_for_update())
    for row in rows:
        if state == "running":
            pursuit = await get_pursuit(db, user_id, row.pursuit_id) if row.pursuit_id is not None else None
            if pursuit is not None and pursuit.status != "active":
                continue
            await start_step(
                db,
                user_id,
                row.id,
                business_ref={**json.loads(row.business_ref_json or "{}"), **business_ref},
                restart=True,
            )
        else:
            row = await finish_step(
                db,
                user_id,
                row.id,
                state=state,
                result_ref=result_ref,
                error=error,
                progress=progress,
                next_step=next_step,
            )
            await _record_step_event(db, user_id, row)
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
