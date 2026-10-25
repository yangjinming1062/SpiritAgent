"""生活状态的流程入口，负责会话边界与提交。"""

from collections.abc import Sequence
from copy import deepcopy

from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

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
    advance_pursuit,
    apply_decision,
    create_step,
    ensure_pursuit,
    get_or_create_state,
    record_event,
    record_events,
    start_step,
)
from .settlement import settle_business_steps


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


async def read_pursuits(
    user_id: int, *, status: str | None, cursor: str | None, limit: int, include_total: bool = False
) -> LifePursuitListResponse:
    async with SESSION_LOCAL() as db:
        return await list_pursuits(db, user_id, status=status, cursor=cursor, limit=limit, include_total=include_total)


async def read_events(
    user_id: int, *, pursuit_id: int | None, cursor: str | None, limit: int, include_total: bool = False
) -> LifeEventListResponse:
    async with SESSION_LOCAL() as db:
        return await list_events(
            db, user_id, pursuit_id=pursuit_id, cursor=cursor, limit=limit, include_total=include_total
        )


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
                    "current_scene": deepcopy(row.current_scene_json or {}),
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


async def update_business_steps_in_session(
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
) -> None:
    """同一任务的步骤、事项、事实与投影批量结算，通知由调用方一并提交。"""
    revision = await settle_business_steps(
        db,
        user_id,
        kind=kind,
        business_ref=business_ref,
        state=state,
        result_ref=result_ref,
        error=error,
        progress=progress,
        next_step=next_step,
        additional_events=additional_events,
    )
    if revision is not None:
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"revision": revision})


async def record_events_in_session(
    db: AsyncSession, user_id: int, events: Sequence[LifeEventInput]
) -> list[LifeEventView]:
    records = await record_events(db, user_id, events)
    if records:
        state = await get_or_create_state(db, user_id, lock=True)
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"revision": state.revision})
    return [event_view(row) for row in records]
