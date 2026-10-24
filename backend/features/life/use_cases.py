"""生活状态的流程入口，负责会话边界与提交。"""

import json

from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeStep
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
    LifeStepView,
)
from .service import (
    apply_decision,
    create_step,
    ensure_pursuit,
    finish_step,
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


async def commit_decision(user_id: int, decision: LifeDecisionRequest) -> LifeStateView:
    async with SESSION_LOCAL() as db:
        previous = await get_life_state_view(db, user_id)
        row = await apply_decision(db, user_id, decision)
        activity_changed = previous is None or previous.current_activity != row.current_activity
        scene_changed = previous is None or previous.scene_revision != row.scene_revision
        if activity_changed or scene_changed:
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
                    },
                    result_ref={"revision": row.revision},
                    is_meaningful=activity_changed,
                ),
            )
            await db.refresh(row)
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"revision": row.revision})
        await db.commit()
        return state_view(row)


async def accept_event_in_session(
    db: AsyncSession,
    user_id: int,
    event: LifeEventInput,
) -> LifeEventView:
    """在调用方事务内接纳事件；只返回公开 DTO。"""
    return event_view(await record_event(db, user_id, event))


async def accept_event(user_id: int, event: LifeEventInput) -> LifeEventView:
    async with SESSION_LOCAL() as db:
        view = await accept_event_in_session(db, user_id, event)
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"event_key": view.event_key})
        await db.commit()
        return view


async def begin_pursuit(
    user_id: int,
    *,
    title: str,
    reason: str = "",
    desired_result: str = "",
    idempotency_key: str | None = None,
) -> LifePursuitView:
    async with SESSION_LOCAL() as db:
        row = await ensure_pursuit(
            db,
            user_id,
            title=title,
            reason=reason,
            desired_result=desired_result,
            idempotency_key=idempotency_key,
        )
        await record_event(
            db,
            user_id,
            LifeEventInput(
                event_key=f"pursuit:{row.id}:started",
                kind="life.pursuit.started",
                source="life_pursuit",
                pursuit_id=row.id,
                fact={
                    "title": row.title,
                    "reason": row.reason,
                    "desired_result": row.desired_result,
                },
                result_ref={"pursuit_id": row.id},
            ),
        )
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"pursuit_id": row.id})
        await db.commit()
        return pursuit_view(row)


async def plan_step(
    user_id: int,
    *,
    idempotency_key: str,
    kind: str,
    description: str,
    pursuit_id: int | None = None,
) -> LifeStepView:
    async with SESSION_LOCAL() as db:
        row = await create_step(
            db,
            user_id,
            idempotency_key=idempotency_key,
            kind=kind,
            description=description,
            pursuit_id=pursuit_id,
        )
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
        await db.commit()
        return step_view(row)


async def _record_step_event(
    db: AsyncSession,
    user_id: int,
    row: LifeStep,
    result_ref: dict | None,
    error: str | None,
) -> None:
    if row.finished_at is None:
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
            fact={"description": row.description, "state": row.state, "error": error or ""},
            result_ref=result_ref or {},
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
                    result_ref=result_ref or {},
                ),
            )


async def _settle_step_row(
    db: AsyncSession,
    user_id: int,
    step_id: int,
    *,
    state: str,
    result_ref: dict | None,
    error: str | None,
    progress: str | None = None,
    next_step: str | None = None,
) -> LifeStep:
    row = await finish_step(
        db,
        user_id,
        step_id,
        state=state,
        result_ref=result_ref,
        error=error,
        progress=progress,
        next_step=next_step,
    )
    await _record_step_event(db, user_id, row, result_ref, error)
    return row


async def settle_step(
    user_id: int,
    step_id: int,
    *,
    state: str,
    result_ref: dict | None = None,
    error: str | None = None,
    progress: str | None = None,
    next_step: str | None = None,
) -> LifeStepView:
    async with SESSION_LOCAL() as db:
        row = await _settle_step_row(
            db,
            user_id,
            step_id,
            state=state,
            result_ref=result_ref,
            error=error,
            progress=progress,
            next_step=next_step,
        )
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
        await db.commit()
        return step_view(row)


async def start_planned_step(user_id: int, step_id: int, *, business_ref: dict | None = None) -> LifeStepView:
    async with SESSION_LOCAL() as db:
        row = await start_step(db, user_id, step_id, business_ref=business_ref)
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
        await db.commit()
        return step_view(row)


async def restart_step_by_key(
    user_id: int,
    idempotency_key: str,
    *,
    business_ref: dict | None = None,
) -> LifeStepView | None:
    """显式重试时恢复失败步骤；其他终态保持不变。"""
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(LifeStep)
            .where(LifeStep.user_id == user_id, LifeStep.idempotency_key == idempotency_key)
            .with_for_update(),
        )
        if row is None:
            return None
        row = await start_step(db, user_id, row.id, business_ref=business_ref, restart=True)
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
        await db.commit()
        return step_view(row)


async def settle_step_by_key(
    user_id: int,
    idempotency_key: str,
    *,
    state: str,
    result_ref: dict | None = None,
    error: str | None = None,
    progress: str | None = None,
    next_step: str | None = None,
) -> LifeStepView | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(LifeStep)
            .where(LifeStep.user_id == user_id, LifeStep.idempotency_key == idempotency_key)
            .with_for_update(),
        )
        if row is None:
            return None
        row = await _settle_step_row(
            db,
            user_id,
            row.id,
            state=state,
            result_ref=result_ref,
            error=error,
            progress=progress,
            next_step=next_step,
        )
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
        await db.commit()
        return step_view(row)
