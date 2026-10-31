"""生活状态的流程入口，负责会话边界与提交。"""

from collections.abc import Sequence
from copy import deepcopy
from datetime import timedelta

from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .execution import enqueue_action
from .lifecycle import transition_pursuit
from .models import LifeEvent, LifeState, LifeStep
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
    LifeEventPublicListResponse,
    LifeEventView,
    LifePursuitDetail,
    LifePursuitListResponse,
    LifePursuitPublicDetail,
    LifePursuitPublicListResponse,
    LifePursuitView,
    LifeStatePublicView,
    LifeStateView,
    LifeStepPublicView,
    LifeStepRequest,
    LifeStepView,
)
from .service import (
    LifeInputError,
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


async def read_public_state(user_id: int) -> LifeStatePublicView:
    return LifeStatePublicView.model_validate(await read_state(user_id))


async def read_public_pursuits(
    user_id: int, *, status: str | None, cursor: str | None, limit: int, include_total: bool = False
) -> LifePursuitPublicListResponse:
    response = await read_pursuits(user_id, status=status, cursor=cursor, limit=limit, include_total=include_total)
    return LifePursuitPublicListResponse.model_validate(response, from_attributes=True)


async def read_step(user_id: int, step_id: int) -> LifeStepView | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        return step_view(row) if row else None


async def read_public_step(user_id: int, step_id: int) -> LifeStepPublicView | None:
    step = await read_step(user_id, step_id)
    return LifeStepPublicView.model_validate(step) if step is not None else None


async def step_for_request(db: AsyncSession, user_id: int, request_id: str) -> LifeStepView | None:
    row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.idempotency_key == request_id))
    return step_view(row) if row else None


async def require_step_submission(db: AsyncSession, user_id: int, request: LifeStepRequest | None) -> None:
    if request is None:
        return
    await get_or_create_state(db, user_id, lock=True)
    row = await db.scalar(
        select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.idempotency_key == request.idempotency_key)
    )
    if row is not None and row.action_json is not None and row.state != "running":
        raise LifeInputError("步骤未取得执行资格或已被暂停、结束")
    if request.pursuit_id is not None:
        pursuit = await get_pursuit(db, user_id, request.pursuit_id)
        if pursuit is None or pursuit.status != "active":
            raise LifeInputError("事项已暂停或结束，不能提交新的执行")


async def defer_review(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        state = await get_or_create_state(db, user_id, lock=True)
        if state.next_review_at is None or ensure_utc(state.next_review_at) <= utc_now():
            state.next_review_at = utc_now() + timedelta(minutes=15)
            state.revision += 1
            await db.commit()


async def pending_presentation_users(db: AsyncSession, *, user_id: int | None = None) -> set[int]:
    query = select(LifeState.user_id).where(LifeState.presentation_evaluated_revision < LifeState.presentation_revision)
    if user_id is not None:
        query = query.where(LifeState.user_id == user_id)
    return set(await db.scalars(query))


async def mark_presentation_evaluated(user_id: int, revision: int) -> None:
    async with SESSION_LOCAL() as db:
        state = await get_or_create_state(db, user_id, lock=True)
        if state.presentation_revision == revision:
            state.presentation_evaluated_revision = revision
            await db.commit()


async def current_presentation_revision(db: AsyncSession, user_id: int) -> int:
    state = await get_or_create_state(db, user_id, lock=True)
    return state.presentation_revision


async def presentation_revision_matches(db: AsyncSession, user_id: int, revision: int) -> bool:
    return await current_presentation_revision(db, user_id) == revision


async def read_pursuit(user_id: int, pursuit_id: int) -> LifePursuitDetail | None:
    async with SESSION_LOCAL() as db:
        row = await get_pursuit(db, user_id, pursuit_id)
        if row is None:
            return None
        steps = await db.scalars(
            select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.pursuit_id == pursuit_id).order_by(LifeStep.id)
        )
        return LifePursuitDetail(**pursuit_view(row).model_dump(), steps=[step_view(step) for step in steps])


async def read_public_pursuit(user_id: int, pursuit_id: int) -> LifePursuitPublicDetail | None:
    detail = await read_pursuit(user_id, pursuit_id)
    return LifePursuitPublicDetail.model_validate(detail) if detail is not None else None


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


async def read_public_events(
    user_id: int, *, pursuit_id: int | None, cursor: str | None, limit: int, include_total: bool = False
) -> LifeEventPublicListResponse:
    response = await read_events(
        user_id, pursuit_id=pursuit_id, cursor=cursor, limit=limit, include_total=include_total
    )
    return LifeEventPublicListResponse.model_validate(response, from_attributes=True)


async def commit_decision_in_session(db: AsyncSession, user_id: int, decision: LifeDecisionRequest) -> LifeStateView:
    row = await get_or_create_state(db, user_id, lock=True)
    request_payload = decision.model_dump(mode="json", exclude={"expected_revision", "request_id", "next_review_at"})
    if decision.request_id:
        previous_command = await db.scalar(
            select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.event_key == decision.request_id)
        )
        if previous_command is not None:
            if previous_command.fact_json.get("request") != request_payload:
                raise ValueError("同一调整请求不能修改其内容")
            return state_view(row)
    previous = state_view(row)
    state_text_changed = any(
        value is not None and value != previous_value
        for value, previous_value in (
            (decision.current_activity, previous.current_activity),
            (decision.activity_kind, previous.activity_kind),
            (decision.activity_type, previous.activity_type),
            (decision.current_scene, previous.current_scene),
            (decision.scene_tags, previous.scene_tags),
            (decision.mood, previous.mood),
        )
    )
    if decision.operation == "keep":
        if state_text_changed or decision.next_action is not None or any(decision.narrative.model_dump().values()):
            raise LifeInputError("保持现状只能安排复查，不能夹带生活或叙事变更")
    else:
        if state_text_changed and not decision.narrative.state:
            raise LifeInputError("改变生活状态必须提供伙伴叙事")
        if (
            decision.operation
            in {
                "start_pursuit",
                "advance_pursuit",
                "complete_pursuit",
                "pause_pursuit",
                "resume_pursuit",
                "abandon_pursuit",
                "redirect_pursuit",
            }
            and not decision.narrative.pursuit
        ):
            raise LifeInputError("调整持续事项必须提供伙伴叙事")
        if decision.next_action is not None and not (decision.narrative.action or decision.next_action.narrative):
            raise LifeInputError("登记生活步骤必须提供伙伴叙事")
        if not decision.narrative.event:
            raise LifeInputError("生活变更必须提供本次变化的伙伴叙事")
    apply_decision(row, decision)
    if decision.narrative.state is not None:
        row.narrative = decision.narrative.state
    await db.flush()
    activity_changed = (
        previous.current_activity != row.current_activity
        or previous.activity_kind != row.activity_kind
        or previous.activity_type != row.activity_type
    )
    scene_changed = previous.scene_revision != row.scene_revision
    mood_changed = previous.mood != row.mood
    if activity_changed or scene_changed or mood_changed:
        await record_event(
            db,
            user_id,
            LifeEventInput(
                event_key=f"life-state:{row.source_key}:revision:{row.revision}",
                kind="life.state.changed",
                source="life_decision",
                fact_type=row.activity_kind,
                fact={
                    "current_activity": row.current_activity,
                    "activity_type": row.activity_type,
                    "scene_tags": list(row.scene_tags_json),
                    "current_scene": deepcopy(row.current_scene_json or {}),
                    "scene_revision": row.scene_revision,
                    "mood": row.mood,
                },
                narrative=decision.narrative.event or decision.narrative.state,
                result_ref={"revision": row.revision},
            ),
        )
    elif decision.operation == "update" and decision.narrative.event:
        await record_event(
            db,
            user_id,
            LifeEventInput(
                event_key=f"life-note:{row.source_key}:revision:{row.revision}",
                kind="life.state.changed",
                source="life_decision",
                fact_type=row.activity_kind,
                fact={"current_activity": row.current_activity, "mood": row.mood},
                narrative=decision.narrative.event,
                pursuit_id=row.active_pursuit_id,
            ),
        )
    previous_pursuit = None
    if decision.operation in {"pause_pursuit", "resume_pursuit", "abandon_pursuit", "redirect_pursuit"}:
        previous_pursuit = await transition_pursuit(db, user_id, decision)
    if decision.operation in {"start_pursuit", "redirect_pursuit"}:
        if not decision.pursuit_reason.strip() or not decision.pursuit_result.strip():
            raise ValueError("新事项必须说明缘由和可核验的完成标准")
        pursuit = await begin_pursuit_in_session(
            db,
            user_id,
            title=decision.pursuit_title,
            reason=decision.pursuit_reason,
            desired_result=decision.pursuit_result,
            narrative=decision.narrative.pursuit,
            idempotency_key=f"life:{decision.expected_revision}:{decision.pursuit_title.strip()}",
        )
        row.active_pursuit_id = pursuit.id
        created = await get_pursuit(db, user_id, pursuit.id)
        if created is not None:
            created.next_step = decision.next_step or ""
            if decision.narrative.pursuit is not None:
                created.narrative = decision.narrative.pursuit
            created.status_source = decision.source
            if previous_pursuit is not None:
                created.previous_pursuit_id = previous_pursuit.id
    elif decision.operation in {"advance_pursuit", "complete_pursuit"}:
        await advance_pursuit(db, user_id, decision)
    if decision.next_action is not None and decision.operation != "keep":
        if row.active_pursuit_id is None:
            raise ValueError("实际下一步须属于持续事项，请先开始事项")
        if decision.operation in {"complete_pursuit", "pause_pursuit", "abandon_pursuit"}:
            raise ValueError("结束或暂停事项时不能登记新步骤")
        next_action = decision.next_action
        if (
            next_action.narrative is not None
            and decision.narrative.action is not None
            and next_action.narrative != decision.narrative.action
        ):
            raise LifeInputError("同一行动的伙伴叙事必须一致")
        if decision.narrative.action is not None:
            next_action = next_action.model_copy(update={"narrative": decision.narrative.action})
        await enqueue_action(
            db,
            user_id,
            next_action,
            request_id=f"life:{row.source_key}:{decision.expected_revision}:action",
            pursuit_id=row.active_pursuit_id,
        )
    if row.presentation_revision != previous.presentation_revision:
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.life.presentation_requested",
            payload={"revision": row.presentation_revision},
        )
    await db.flush()
    await db.refresh(row)
    if decision.request_id:
        await record_event(
            db,
            user_id,
            LifeEventInput(
                event_key=decision.request_id,
                kind="life.command.applied",
                source=decision.source,
                fact_type="intent",
                is_meaningful=False,
                fact={"operation": decision.operation, "request": request_payload},
                narrative=decision.narrative.event,
            ),
        )
    emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"revision": row.revision})
    return state_view(row)


async def accept_event_in_session(db: AsyncSession, user_id: int, event: LifeEventInput) -> LifeEventView:
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
    narrative: str | None = None,
    idempotency_key: str | None = None,
) -> LifePursuitView:
    row = await ensure_pursuit(
        db,
        user_id,
        title=title,
        reason=reason,
        desired_result=desired_result,
        narrative=narrative,
        idempotency_key=idempotency_key,
    )
    await record_event(
        db,
        user_id,
        LifeEventInput(
            event_key=f"pursuit:{row.id}:started",
            kind="life.pursuit.started",
            source="life_pursuit",
            fact_type="intent",
            pursuit_id=row.id,
            fact={"title": row.title, "reason": row.reason, "desired_result": row.desired_result},
            narrative=narrative,
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
    failure_code: str | None = None,
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
        failure_code=failure_code,
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
