"""生活状态的流程入口，负责会话边界与提交。"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from secrets import token_hex
from typing import Any

from infrastructure.database import SESSION_LOCAL
from infrastructure.library_visibility import release_private_work, released_work, visible_library_work
from infrastructure.logger import get_logger
from infrastructure.text import ensure_utc, utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.timezone import resolve_user_timezone

from .actions import CREATION_CAPABILITIES
from .episode_media import (
    EpisodeOriginalResolver,
    add_generated_episode_media,
    add_original_episode_media,
    episode_asset_ids,
    link_episode_share_topic,
)
from .execution import enqueue_action, notify_shared_step
from .lifecycle import transition_pursuit
from .models import LifeEvent, LifeGrowth, LifeState, LifeStep
from .queries import (
    empty_state_view,
    event_view,
    get_life_state_view,
    get_pursuit,
    list_pursuits,
    project_public_completions,
    pursuit_view,
    shared_pursuit_ids,
    state_view,
    step_view,
)
from .schemas import (
    EpisodeImagesPlan,
    EpisodeOriginalPlan,
    EpisodeVideoPlan,
    LifeDecisionRequest,
    LifeEventInput,
    LifeEventView,
    LifePursuitDetail,
    LifePursuitListResponse,
    LifePursuitPublicDetail,
    LifePursuitPublicListResponse,
    LifePursuitView,
    LifeShare,
    LifeStatePublicView,
    LifeStateView,
    LifeStepRequest,
    LifeStepView,
    LifeTrajectoryListResponse,
    PersonalGrowthChange,
    PrivateLifeEpisode,
)
from .service import (
    LifeInputError,
    LifeStateConflictError,
    advance_pursuit,
    apply_decision,
    assign_result,
    clear_activity_step,
    create_step,
    ensure_pursuit,
    get_or_create_state,
    record_event,
    start_step,
)
from .settlement import settle_business_steps
from .trajectory import list_trajectory, public_result_reference

logger = get_logger(__name__)


async def read_state(user_id: int) -> LifeStateView:
    async with SESSION_LOCAL() as db:
        return await get_life_state_view(db, user_id) or empty_state_view(user_id)


async def read_public_state(user_id: int) -> LifeStatePublicView:
    async with SESSION_LOCAL() as db:
        state = await get_life_state_view(db, user_id) or empty_state_view(user_id)
        latest = (await list_trajectory(db, user_id, limit=1)).items
        view = LifeStatePublicView.model_validate(state)
        view.latest_episode = latest[0] if latest else None
        if not state.activity_shared_with_user:
            view.activity_kind, view.activity_type, view.activity_started_at = "virtual_activity", "idle", None
        if state.active_pursuit_id is not None and not await shared_pursuit_ids(db, user_id, {state.active_pursuit_id}):
            view.active_pursuit_id = None
        return view


async def read_public_trajectory(
    user_id: int, *, pursuit_id: int | None, topic_id: int | None, cursor: str | None, limit: int
) -> LifeTrajectoryListResponse:
    async with SESSION_LOCAL() as db:
        return await list_trajectory(db, user_id, pursuit_id=pursuit_id, topic_id=topic_id, cursor=cursor, limit=limit)


async def read_public_pursuits(
    user_id: int, *, status: str | None, cursor: str | None, limit: int
) -> LifePursuitPublicListResponse:
    async with SESSION_LOCAL() as db:
        response = await list_pursuits(db, user_id, status=status, cursor=cursor, limit=limit, shared_only=True)
    return LifePursuitPublicListResponse.model_validate(response, from_attributes=True)


async def read_step(user_id: int, step_id: int) -> LifeStepView | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        return step_view(row) if row else None


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


DEFAULT_REVIEW_DELAY = timedelta(hours=2)


async def reserve_review_in_session(db: AsyncSession, user_id: int, now: datetime) -> tuple[bool, str | None]:
    """先持久认领本次判断。提交前保留可恢复的下一次期限。"""
    state = await get_or_create_state(db, user_id, lock=True)
    if state.next_review_at is not None and ensure_utc(state.next_review_at) > now:
        return False, None
    timezone = await resolve_user_timezone(db, user_id)
    if timezone is None:
        state.next_review_at = now + timedelta(hours=1)
        return False, None
    state.next_review_at = now + DEFAULT_REVIEW_DELAY
    state.review_slot = token_hex(8)
    return True, state.review_slot


async def set_next_review_in_session(
    db: AsyncSession, user_id: int, *, slot: str, expected_revision: int, review_after_minutes: int | None
) -> None:
    row = await get_or_create_state(db, user_id, lock=True)
    if row.revision != expected_revision or row.review_slot != slot:
        raise LifeStateConflictError("生活状态或判断已在其他流程更新")
    row.next_review_at = utc_now() + (
        timedelta(minutes=review_after_minutes) if review_after_minutes is not None else DEFAULT_REVIEW_DELAY
    )


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


async def read_pursuit(user_id: int, pursuit_id: int, *, shared_only: bool = False) -> LifePursuitDetail | None:
    async with SESSION_LOCAL() as db:
        row = await get_pursuit(db, user_id, pursuit_id)
        if row is None or (shared_only and not row.shared_with_user):
            return None
        steps = await db.scalars(
            select(LifeStep)
            .where(
                LifeStep.user_id == user_id,
                LifeStep.pursuit_id == pursuit_id,
                *([visible_library_work(LifeStep.library_work_id)] if shared_only else []),
            )
            .order_by(LifeStep.id)
        )
        pursuit = pursuit_view(row)
        if shared_only:
            await project_public_completions(db, user_id, [pursuit])
        if (
            shared_only
            and pursuit.previous_pursuit_id is not None
            and not await shared_pursuit_ids(db, user_id, {pursuit.previous_pursuit_id})
        ):
            pursuit.previous_pursuit_id = None
        return LifePursuitDetail(**pursuit.model_dump(), steps=[step_view(step) for step in steps])


async def read_public_pursuit(user_id: int, pursuit_id: int) -> LifePursuitPublicDetail | None:
    detail = await read_pursuit(user_id, pursuit_id, shared_only=True)
    return LifePursuitPublicDetail.model_validate(detail) if detail is not None else None


async def read_pursuits(user_id: int, *, status: str | None, cursor: str | None, limit: int) -> LifePursuitListResponse:
    async with SESSION_LOCAL() as db:
        return await list_pursuits(db, user_id, status=status, cursor=cursor, limit=limit)


_PURSUIT_OPERATIONS = frozenset(
    {
        "start_pursuit",
        "advance_pursuit",
        "complete_pursuit",
        "pause_pursuit",
        "resume_pursuit",
        "abandon_pursuit",
        "redirect_pursuit",
    }
)


def _state_text_changed(decision: LifeDecisionRequest, previous: LifeStateView) -> bool:
    return any(
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


def _validate_decision(decision: LifeDecisionRequest, previous: LifeStateView) -> None:
    """一次判断只能改变声明过的部分，并携带对应的伙伴叙事。"""
    state_text_changed = _state_text_changed(decision, previous)
    if decision.operation == "keep":
        if state_text_changed or decision.next_action is not None or any(decision.narrative.model_dump().values()):
            raise LifeInputError("保持现状只能安排复查，不能夹带生活或叙事变更")
        return
    if state_text_changed and not decision.narrative.state:
        raise LifeInputError("改变生活状态必须提供伙伴叙事")
    if decision.operation in _PURSUIT_OPERATIONS and not decision.narrative.pursuit:
        raise LifeInputError("调整持续事项必须提供伙伴叙事")
    if not decision.narrative.event:
        raise LifeInputError("生活变更必须提供本次变化的伙伴叙事")


async def commit_private_episode_in_session(
    db: AsyncSession,
    user_id: int,
    episode: PrivateLifeEpisode,
    *,
    slot: str,
    expected_revision: int,
    awake_until: datetime,
    show_activity: bool = False,
) -> LifeEventView:
    row = await get_or_create_state(db, user_id, lock=True)
    if row.revision != expected_revision or row.review_slot != slot:
        raise LifeStateConflictError("生活状态或日常时段已经变化")
    now = utc_now()
    if now >= awake_until:
        raise LifeStateConflictError("伙伴已休息")
    previous_presentation = row.presentation_revision
    if row.activity_step_id is None:
        apply_decision(
            row,
            LifeDecisionRequest(
                expected_revision=expected_revision,
                operation="update",
                current_activity=episode.title,
                activity_kind="virtual_activity",
                activity_type=episode.activity_type,
                current_scene=episode.current_scene,
                scene_tags=episode.scene_tags,
            ),
        )
        row.narrative = episode.narrative
        if row.activity_shared_with_user != show_activity:
            row.activity_shared_with_user = show_activity
            row.presentation_revision += 1
    view = await record_event(
        db,
        user_id,
        LifeEventInput(
            event_key=f"life-episode:{slot}",
            occurred_at=now,
            kind="life.episode.recorded",
            source="life_decision",
            fact_type="virtual_activity",
            fact={"title": episode.title},
            narrative=episode.narrative,
            shared_with_user=False,
            trajectory_kind="virtual",
            previous_episode_id=episode.previous_episode_id,
        ),
    )
    if row.presentation_revision != previous_presentation:
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.life.presentation_requested",
            payload={"revision": row.presentation_revision},
        )
    return event_view(view)


async def share_life_event_in_session(
    db: AsyncSession,
    user_id: int,
    share: LifeShare,
    source_id: int,
    *,
    original_resolver: EpisodeOriginalResolver | None = None,
    media_plans: Sequence[dict[str, Any]] = (),
) -> LifeEventView | None:
    """按所选渠道保存摘选，保留来源记录及其可见范围。"""
    await get_or_create_state(db, user_id, lock=True)
    original = await db.scalar(select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.id == source_id))
    if original is None:
        raise LifeInputError("分享来源不属于当前生活记录")
    if original.source == "life_share":
        return None
    step = await db.get(LifeStep, original.step_id) if original.step_id is not None else None
    work_id = step.library_work_id if step else None
    public_artifact = work_id is None or await released_work(db, user_id, work_id)
    result = public_result_reference(original)
    release = False
    if share.release_work:
        if (
            work_id is None
            or step is None
            or step.state != "completed"
            or step.kind not in CREATION_CAPABILITIES
            or original.kind != "life.step.completed"
            or not result
            or ("visual_id" in result and "visual_version_id" not in result)
        ):
            if not isinstance(share.media, EpisodeOriginalPlan):
                raise LifeInputError("原作品已不可用")
            public_artifact = False
        elif not public_artifact:
            release = await release_private_work(db, user_id, work_id)
    if not release and await db.scalar(
        select(LifeEvent.id)
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.source == "life_share",
            LifeEvent.fact_json["event_id"].as_string() == str(source_id),
            LifeEvent.fact_json["channel"].as_string() == share.channel,
        )
        .limit(1)
    ):
        return None
    if release:
        public_artifact = True
        if "scene_id" in result:
            emit_ws_event(
                db, user_id=user_id, event_type="companion.scene.updated", payload={"scene_id": result["scene_id"]}
            )
        elif "outfit_id" in result:
            emit_ws_event(
                db, user_id=user_id, event_type="companion.outfit.updated", payload={"outfit_id": result["outfit_id"]}
            )
        elif "action_id" in result:
            emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
        elif "visual_id" in result:
            emit_ws_event(
                db, user_id=user_id, event_type="companion.life.presentation_requested", payload={"force": True}
            )
        if "scene_id" in result or "action_id" in result:
            emit_ws_event(db, user_id=user_id, event_type="companion.visual.preparation_requested", payload={})
    public = share.channel == "life"
    media = share.media
    asset = None
    asset_ids = None
    if (
        public
        and public_artifact
        and original_resolver
        and (isinstance(media, EpisodeOriginalPlan) or share.release_work)
        and (asset := await original_resolver(db, user_id, result))
    ):
        try:
            async with db.begin_nested():
                asset_ids = await episode_asset_ids(db, user_id, asset)
        except (OSError, ValueError):
            logger.info("Life share original asset is unavailable", extra={"user_id": user_id, "event_id": source_id})
    if isinstance(media, EpisodeImagesPlan):
        items, kind = media.images, "image"
    elif isinstance(media, EpisodeVideoPlan):
        items, kind = [media], "video"
    else:
        items, kind = [], "image"
    generate = bool(items) and len(items) == len(media_plans)
    visible = public and (share.narrative is not None or asset_ids is not None)
    if public and not visible and not generate:
        return None
    episode = await record_event(
        db,
        user_id,
        LifeEventInput(
            event_key=f"life-share:{token_hex(12)}",
            kind="life.episode.shared" if public else "life.share.prepared",
            source="life_share",
            fact_type=original.fact_type,
            fact={"title": share.title, "channel": share.channel, "event_id": str(source_id)},
            narrative=share.narrative,
            occurred_at=original.occurred_at,
            # 摘选有自己的轨迹身份，成果事实和完整阅读笔记仍属于来源事件。
            trajectory_kind=("virtual" if original.trajectory_kind == "virtual" else "milestone") if public else None,
            result_ref=result if public and public_artifact else {},
            shared_with_user=visible,
        ),
    )
    if public:
        await link_episode_share_topic(db, episode, original)
    if asset is not None and asset_ids is not None:
        alt = media.alt if isinstance(media, EpisodeOriginalPlan) else share.title
        add_original_episode_media(db, user_id, episode.id, asset.kind, alt, asset_ids)
    if generate:
        try:
            async with db.begin_nested():
                await add_generated_episode_media(db, user_id, episode.id, kind, items, media_plans)
        except (OSError, ValueError):
            logger.info("Life share media inputs are unavailable", extra={"user_id": user_id, "event_id": source_id})
    if visible:
        emit_ws_event(
            db, user_id=user_id, event_type="companion.life.updated", payload={"event_key": episode.event_key}
        )
    return event_view(episode)


async def update_personal_growth_in_session(
    db: AsyncSession, user_id: int, change: PersonalGrowthChange, evidence: LifeEventView | None
) -> None:
    state = await get_or_create_state(db, user_id, lock=True)
    existing = None
    if change.growth_id is not None:
        existing = await db.scalar(
            select(LifeGrowth).where(LifeGrowth.user_id == user_id, LifeGrowth.id == change.growth_id)
        )
        if existing is None or existing.aspect != change.aspect:
            raise LifeStateConflictError("选定的认识已失效")
    if change.text is None:
        if existing is not None:
            await db.delete(existing)
    else:
        if (
            evidence is None
            or evidence.subject != "companion"
            or evidence.fact_type not in ("virtual_activity", "result")
        ):
            raise LifeInputError("个人认识需要来自伙伴自己的经历或已确认成果")
        if existing is None:
            existing = await db.scalar(
                select(LifeGrowth)
                .where(
                    LifeGrowth.user_id == user_id,
                    LifeGrowth.aspect == change.aspect,
                    LifeGrowth.text == change.text,
                )
                .limit(1)
            )
        if existing is None:
            existing = LifeGrowth(user_id=user_id, aspect=change.aspect)
            db.add(existing)
        if (
            existing.text == change.text
            and existing.outlook == change.outlook
            and existing.evidence_event_id == evidence.id
        ):
            return
        existing.text, existing.outlook, existing.evidence_event_id = change.text, change.outlook, evidence.id
    state.revision += 1


async def _apply_pursuit_operation(
    db: AsyncSession, user_id: int, row: LifeState, decision: LifeDecisionRequest, successor_focus: int | None
) -> None:
    previous_pursuit = None
    if decision.operation in {"pause_pursuit", "resume_pursuit", "abandon_pursuit", "redirect_pursuit"}:
        previous_pursuit = await transition_pursuit(db, user_id, decision, successor_focus=successor_focus)
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
            previous_episode_id=decision.episode_origin_id,
            previous_pursuit_id=previous_pursuit.id if previous_pursuit is not None else None,
            source_message_id=decision.source_message_id,
            shared_with_user=decision.source == "user"
            or (previous_pursuit is not None and previous_pursuit.shared_with_user),
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
        await advance_pursuit(db, user_id, decision, successor_focus=successor_focus)


async def _enqueue_next_action(db: AsyncSession, user_id: int, row: LifeState, decision: LifeDecisionRequest) -> None:
    if decision.next_action is None or decision.operation == "keep":
        return
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
        request_id=f"life:{user_id}:{decision.expected_revision}:action",
        pursuit_id=row.active_pursuit_id,
    )


async def commit_decision_in_session(
    db: AsyncSession, user_id: int, decision: LifeDecisionRequest, *, successor_focus: int | None = None
) -> LifeStateView:
    row = await get_or_create_state(db, user_id, lock=True)
    if decision.operation == "start_pursuit" and decision.source == "user" and decision.source_message_id is None:
        raise LifeInputError("登记用户约定需要本轮实际用户发言")
    request_payload = decision.model_dump(mode="json", exclude={"expected_revision", "request_id", "next_review_at"})
    if decision.request_id:
        previous_command = await db.scalar(
            select(LifeEvent).where(LifeEvent.user_id == user_id, LifeEvent.event_key == decision.request_id)
        )
        if previous_command is not None:
            if previous_command.fact_json.get("request") != request_payload:
                raise ValueError("同一调整请求不能修改其内容")
            return state_view(row)
    if decision.operation == "note_exchange":
        if (
            decision.source != "user"
            or decision.source_message_id is None
            or decision.narrative.event is None
            or decision.next_action is not None
        ):
            raise LifeInputError("记录交流需要本轮实际用户发言")
        event_key = f"life-exchange:{decision.source_message_id}"
        if await db.scalar(select(LifeEvent.id).where(LifeEvent.user_id == user_id, LifeEvent.event_key == event_key)):
            return state_view(row)
        if decision.expected_revision != row.revision:
            raise LifeStateConflictError("生活状态已变化，请重新读取")
        await accept_event_in_session(
            db,
            user_id,
            LifeEventInput(
                event_key=event_key,
                kind="life.exchange.noted",
                source="conversation",
                fact_type="execution",
                fact={"title": decision.pursuit_title or None, "message_id": str(decision.source_message_id)},
                narrative=decision.narrative.event,
                trajectory_kind="milestone",
                shared_with_user=True,
            ),
        )
        return state_view(row)
    previous = state_view(row)
    _validate_decision(decision, previous)
    apply_decision(row, decision)
    if decision.narrative.state is not None:
        row.narrative = decision.narrative.state
    await db.flush()
    await _apply_pursuit_operation(db, user_id, row, decision, successor_focus)
    await _enqueue_next_action(db, user_id, row, decision)
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
    if decision.source == "user" or await shared_pursuit_ids(
        db,
        user_id,
        {identifier for identifier in (decision.pursuit_id, row.active_pursuit_id) if identifier is not None},
    ):
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"revision": row.revision})
    return state_view(row)


async def accept_event_in_session(db: AsyncSession, user_id: int, event: LifeEventInput) -> LifeEventView:
    """在调用方事务内接纳事件；只返回公开 DTO。"""
    view = event_view(await record_event(db, user_id, event))
    if view.shared_with_user:
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
    previous_episode_id: int | None = None,
    previous_pursuit_id: int | None = None,
    source_message_id: int | None = None,
    shared_with_user: bool = False,
) -> LifePursuitView:
    row = await ensure_pursuit(
        db,
        user_id,
        title=title,
        reason=reason,
        desired_result=desired_result,
        narrative=narrative,
        idempotency_key=idempotency_key,
        shared_with_user=shared_with_user,
    )
    if previous_pursuit_id is not None:
        previous_episode_id = await db.scalar(
            select(LifeEvent.id)
            .where(
                LifeEvent.user_id == user_id,
                LifeEvent.pursuit_id == previous_pursuit_id,
                LifeEvent.trajectory_kind.is_not(None),
            )
            .order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
            .limit(1)
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
            fact={
                "title": row.title,
                "reason": row.reason,
                "desired_result": row.desired_result,
                **({"message_id": str(source_message_id), "source": "user"} if source_message_id is not None else {}),
            },
            narrative=narrative,
            result_ref={"pursuit_id": row.id},
            trajectory_kind="milestone",
            previous_episode_id=previous_episode_id,
        ),
    )
    if row.shared_with_user:
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"pursuit_id": row.id})
    return pursuit_view(row)


async def bind_step_in_session(
    db: AsyncSession, user_id: int, request: LifeStepRequest, *, business_ref: dict
) -> LifeStepView:
    row = await create_step(db, user_id, **request.model_dump())
    row = await start_step(db, user_id, row.id, business_ref=business_ref)
    await notify_shared_step(db, user_id, row)
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
    await settle_business_steps(
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


async def wait_business_media_in_session(
    db: AsyncSession, user_id: int, kind: str, job_id: int, reason: str, *, code: str
) -> None:
    states = ("running", "failed", "unknown") if code == "media_resume_waiting" else ("running",)
    linked = select(LifeStep).where(
        LifeStep.user_id == user_id, LifeStep.kind == kind, LifeStep.job_id == job_id, LifeStep.state.in_(states)
    )
    if not await db.scalar(linked.with_only_columns(LifeStep.id).limit(1)):
        return
    state = await get_or_create_state(db, user_id, lock=True)
    rows = list(
        await db.scalars(
            linked.where(
                or_(LifeStep.disposition != "waiting", LifeStep.failure_code.is_distinct_from(code))
            ).with_for_update()
        )
    )
    if not rows:
        return
    for row in rows:
        if row.state == "failed":
            assign_result(row, {})
        row.state = "running"
        row.disposition, row.wait_reason, row.failure_code = "waiting", reason, code
        row.finished_at, row.error = None, None
        if state.activity_step_id == row.id:
            clear_activity_step(db, state)
    state.revision += 1
    if await shared_pursuit_ids(db, user_id, {row.pursuit_id for row in rows if row.pursuit_id is not None}):
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"revision": state.revision})


async def resume_business_media_in_session(
    db: AsyncSession, user_id: int, kind: str, job_id: int, *, generation_id: str | None = None
) -> bool:
    linked = select(LifeStep.id).where(
        LifeStep.user_id == user_id,
        LifeStep.kind == kind,
        LifeStep.job_id == job_id,
        LifeStep.state.in_(("running", "failed", "unknown")),
    )
    if not await db.scalar(linked.limit(1)):
        return True
    await get_or_create_state(db, user_id, lock=True)
    if await db.scalar(
        select(LifeStep.id)
        .where(
            LifeStep.user_id == user_id,
            LifeStep.state == "running",
            LifeStep.disposition == "executing",
            LifeStep.job_id.is_distinct_from(job_id),
        )
        .limit(1)
    ):
        return False
    await update_business_steps_in_session(
        db,
        user_id,
        kind=kind,
        business_ref={"job_id": job_id, **({"generation_id": generation_id} if generation_id is not None else {})},
        state="running",
    )
    return True
