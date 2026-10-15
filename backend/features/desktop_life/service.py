"""桌面生活的组合准备、动作评审与播放协调。"""

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Literal
from uuid import uuid4

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import VisualReasoningError, chat, vision_chat
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import (
    read_valid_image_data_uri,
    save_action_source_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
    unlink_companion_asset,
)
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import parse_llm_json, resolve_language, resolve_prompt_text, utc_now
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call
from runtime.user_maintenance import track_user_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.policy import DEFERRED_PLAY_INTENT_TTL_SECONDS, ActionPolicyError, consume_create_slot
from features.actions.repository import normalize_action_name
from features.assets.lifecycle import enqueue_asset_cleanup
from features.assets.references import asset_paths
from features.companion.disturbance import get_disturbance_tier
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.companion.prompt_runtime import load_companion_prompt_context
from features.configuration.settings.values import get_user_setting
from features.conversation.context_window import load_recent_context_window
from features.desktop_life.generation import (
    cancel_desktop_video_job,
    desktop_context_hash,
    desktop_progress_can_resume,
    desktop_progress_has_unknown_submission,
    load_desktop_visual_snapshot,
    prepare_uploaded_desktop_image,
    resolve_desktop_video_plan,
    schedule_desktop_video_job,
)
from features.desktop_life.models import DesktopVideoAction, DesktopVideoPlayback, DesktopVideoProposal, DesktopVideoSet
from features.desktop_life.policy import desktop_autonomous_block_reason
from features.desktop_life.prompts import (
    DESKTOP_ACTION_REVIEW,
    DESKTOP_CONTEXT_GUIDANCE,
    DESKTOP_EXTERNAL_VIDEO_TEMPLATES,
    DESKTOP_IDLE_EXPRESSION,
    DESKTOP_PRESETS,
    DESKTOP_VIDEO_BASE,
    DESKTOP_VIDEO_LOOP,
    DESKTOP_VIDEO_ONCE,
)
from features.desktop_life.queries import activated_desktop_environment
from features.desktop_life.repository import (
    DesktopVideoError,
    DesktopVideoNotFoundError,
    DesktopVideoStateError,
    claim_desktop_play,
    desktop_accepted_asset,
    desktop_action,
    desktop_action_description,
    desktop_action_response,
    desktop_preferences,
    desktop_progress,
    desktop_removed_video_task,
    desktop_set_response,
    desktop_state,
    desktop_state_response,
    emit_desktop_play,
    fulfill_desktop_play_intents,
    publish_desktop_asset,
    record_desktop_receipt,
    require_desktop_current,
    touch_desktop_state,
)
from features.desktop_life.schemas import (
    DesktopVideoActionResponse,
    DesktopVideoCreateSetRequest,
    DesktopVideoDesignRequest,
    DesktopVideoExternalPromptResponse,
    DesktopVideoListResponse,
    DesktopVideoPlayCommand,
    DesktopVideoPlayRequest,
    DesktopVideoPreferences,
    DesktopVideoProgress,
    DesktopVideoProposalResponse,
    DesktopVideoReceipt,
    DesktopVideoReference,
    DesktopVideoSetResponse,
    DesktopVideoStateResponse,
    DesktopVisualSnapshot,
)
from features.media.chain import spawn_removed_action_task_cancellation
from features.media.contracts import RemovedActionVideoTask

logger = get_logger(__name__)
_LOCKS: dict[int, asyncio.Lock] = {}
_REVIEWS: dict[int, asyncio.Task[None]] = {}


def _lock(user_id: int) -> asyncio.Lock:
    return _LOCKS.setdefault(user_id, asyncio.Lock())


def _require_desktop_mode(user_id: int) -> None:
    presentation = get_presentation_snapshot(user_id)
    if presentation is None or presentation.mode != "desktop":
        raise DesktopVideoStateError("桌面模式当前未启用")


async def _wait_desktop_mode(user_id: int) -> None:
    for _attempt in range(20):
        presentation = get_presentation_snapshot(user_id)
        if presentation is not None and presentation.mode == "desktop":
            return
        await asyncio.sleep(0.1)
    _require_desktop_mode(user_id)


async def get_desktop_video_state(db: AsyncSession, user_id: int) -> DesktopVideoStateResponse:
    return await desktop_state_response(db, user_id)


async def list_desktop_video_sets(db: AsyncSession, user_id: int) -> DesktopVideoListResponse:
    state = await desktop_state(db, user_id)
    rows = list(
        await db.scalars(
            select(DesktopVideoSet)
            .where(DesktopVideoSet.user_id == user_id)
            .order_by(DesktopVideoSet.updated_at.desc()),
        ),
    )
    return DesktopVideoListResponse(
        sets=[await desktop_set_response(db, row, current_id=state.current_set_id if state else None) for row in rows],
        version=state.version if state else 0,
    )


async def get_desktop_action_response(db: AsyncSession, user_id: int, action_id: int) -> DesktopVideoActionResponse:
    return desktop_action_response(await desktop_action(db, user_id, action_id, lock=True))


async def get_desktop_external_prompt(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    *,
    expected_set_id: int | None = None,
    requirements: str = "",
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
) -> DesktopVideoExternalPromptResponse:
    action = await desktop_action(db, user_id, action_id, lock=True)
    if expected_set_id is not None and expected_set_id != action.set_id:
        raise DesktopVideoStateError("桌面生活组合已经变化")
    row = await _suite(db, user_id, action.set_id)
    snapshot = DesktopVisualSnapshot.model_validate_json(row.context_json)
    progress = desktop_progress(action) if action.stage != "complete" else None
    _validate_draft(
        row,
        progress if action.stage != "complete" else None,
        draft_id=expected_draft_id,
        base_version=expected_base_version,
    )
    accepted = desktop_accepted_asset(action)
    confirmed = progress.pose_path if progress and progress.image_confirmed and action.stage != "complete" else ""
    confirmed = (
        confirmed
        or (row.base_image_path if action.continuity == "base" else "")
        or (accepted.image_path if accepted else "")
    )
    references: list[DesktopVideoReference] = []
    chosen = (
        [("已确认的起始画面", confirmed)]
        if confirmed
        else [
            ("头像参考图", snapshot.portrait_path),
            ("着装参考图", snapshot.outfit_path),
            ("场景参考图", snapshot.scene_path),
        ]
    )
    for label, path in chosen:
        if not path or not await asyncio.to_thread(read_valid_image_data_uri, path):
            raise DesktopVideoStateError("套装参考图片无法读取，请重新上传图片")
        url = signed_companion_asset_url(path)
        if url is None:
            raise DesktopVideoStateError("套装参考图片无法读取，请重新上传图片")
        references.append(DesktopVideoReference(label=label, url=url))
    has_base = action.continuity == "base" and bool(confirmed)
    language = resolve_language(await get_user_setting(db, user_id, "language"))
    extra = requirements.strip()
    description = desktop_action_description(action)
    if language == "en":
        kind = "looping" if action.kind == "loop" else "single-action"
        cycle = (
            "Image 1 is the required opening and ending frame. Enter the activity from this pose, complete it, then restore the same pose, gaze, object positions, camera and lighting."
            if has_base
            else "The ending joins the opening naturally in pose, gaze and object positions for continuous looping."
            if action.kind == "loop"
            else "Complete the target action once, then hold its natural finishing state."
        )
    else:
        kind = "循环" if action.kind == "loop" else "单次"
        cycle = (
            "图 1 是必须使用的起止画面。" + DESKTOP_VIDEO_BASE
            if has_base
            else DESKTOP_VIDEO_LOOP
            if action.kind == "loop"
            else DESKTOP_VIDEO_ONCE
        )
    prompt = DESKTOP_EXTERNAL_VIDEO_TEMPLATES[language].format(
        duration_seconds=action.duration_seconds,
        references=(
            "图 1 是已确认的起始画面，人物外观、穿着、环境、物品和构图均以此为准。"
            if confirmed and language != "en"
            else "Image 1 is the confirmed opening frame. Preserve its visible identity, clothing, environment, props and framing."
            if confirmed
            else "图 1 提供人物外观，图 2 提供着装，图 3 提供场景环境。"
            if language != "en"
            else "Image 1 provides identity, image 2 clothing, and image 3 the environment."
        ),
        kind=kind,
        motion=description,
        cycle=cycle,
    )
    conditions = {"use_when": json.loads(action.use_when_json), "avoid_when": json.loads(action.avoid_when_json)}
    prompt += (
        "\nUsage conditions: "
        + json.dumps(conditions, ensure_ascii=False)
        + ". Use the actual body, available props and space; if required conditions are missing, explain why instead of redesigning the body or room."
        if language == "en"
        else "\n适用与避免条件："
        + json.dumps(conditions, ensure_ascii=False)
        + "。使用实际身体、已有物品和空间；必要条件缺失时说明原因，不能改造身体或房间布局来完成活动。"
    )
    if extra:
        if confirmed:
            prompt += (
                "\nAdditional user requirements adjust only the activity and expressions compatible with image 1; "
                f"preserve its opening composition and the playback requirements above: {extra}"
                if language == "en"
                else f"\n用户补充要求仅调整与图 1 相容的活动和过程神态，保留图中的开场构图及上述播放要求：{extra}"
            )
        else:
            prompt += (
                "\nAdditional user requirements (take precedence for the specified activity, expression and opening framing; "
                f"preserve the identity, styling, environment and playback mode above): {extra}"
                if language == "en"
                else f"\n用户补充要求（对应的活动、神态与开场构图细节以此为准，身份、造型、环境与播放方式沿用上述要求）：{extra}"
            )
    return DesktopVideoExternalPromptResponse(
        draft_id=progress.generation_id if progress and action.stage != "complete" else None,
        base_version=row.base_version,
        action_id=action.id,
        set_id=action.set_id,
        name=action.name,
        description=description,
        kind=action.kind,
        duration_seconds=action.duration_seconds,
        prompt=prompt,
        references=references,
    )


async def get_desktop_proposal_response(
    db: AsyncSession,
    user_id: int,
    proposal_id: int,
) -> DesktopVideoProposalResponse:
    row = await db.scalar(
        select(DesktopVideoProposal).where(
            DesktopVideoProposal.id == proposal_id,
            DesktopVideoProposal.user_id == user_id,
        ),
    )
    if row is None:
        raise DesktopVideoNotFoundError("找不到对应的桌面动作提案")
    return DesktopVideoProposalResponse(
        proposal_id=row.id,
        set_id=row.set_id,
        action_id=row.action_id,
        status=row.status,
        review_reason=row.review_reason,
    )


async def _suite(db: AsyncSession, user_id: int, set_id: int) -> DesktopVideoSet:
    await desktop_state(db, user_id, create=True)
    row = await db.scalar(
        select(DesktopVideoSet).where(DesktopVideoSet.id == set_id, DesktopVideoSet.user_id == user_id),
    )
    if row is None:
        raise DesktopVideoNotFoundError("找不到对应的画面")
    return row


async def _create_suite(
    db: AsyncSession,
    user_id: int,
    snapshot: DesktopVisualSnapshot,
    *,
    name: str,
    request_id: str | None,
    source: str,
    is_default: bool = False,
) -> DesktopVideoSet:
    row = DesktopVideoSet(
        user_id=user_id,
        avatar_id=snapshot.identity.avatar_id,
        outfit_id=snapshot.outfit_id,
        scene_id=snapshot.scene_id,
        context_hash=desktop_context_hash(snapshot),
        context_json=snapshot.model_dump_json(),
        title=name,
        request_id=request_id,
        is_default=is_default,
        source=source,
        status="preparing",
    )
    db.add(row)
    await db.flush()
    for preset in DESKTOP_PRESETS:
        db.add(
            DesktopVideoAction(
                user_id=user_id,
                set_id=row.id,
                key=preset.key,
                name=preset.name,
                description=preset.description,
                kind=preset.kind,
                duration_seconds=preset.duration_seconds,
                target_duration_seconds=preset.duration_seconds,
                continuity=preset.continuity,
                allowed_triggers_json=json.dumps(preset.allowed_triggers),
                use_when_json=json.dumps(preset.use_when, ensure_ascii=False),
                avoid_when_json=json.dumps(preset.avoid_when, ensure_ascii=False),
                preset=True,
                status="queued",
                stage="prepare",
                enabled=True,
                version=0,
                attempt=0,
                needs_update=False,
                update_pending=False,
            ),
        )
    await db.flush()
    await touch_desktop_state(db, user_id)
    return row


async def create_desktop_video_set(user_id: int, request: DesktopVideoCreateSetRequest) -> DesktopVideoSetResponse:
    idle_id = None
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await desktop_state(db, user_id, create=True)
        row = await db.scalar(
            select(DesktopVideoSet).where(
                DesktopVideoSet.user_id == user_id,
                DesktopVideoSet.request_id == request.request_id,
            ),
        )
        if row is None:
            snapshot = await load_desktop_visual_snapshot(
                db,
                user_id,
                scene_id=request.scene_id,
                outfit_id=request.outfit_id,
            )
            row = await _create_suite(
                db,
                user_id,
                snapshot,
                name=request.name,
                request_id=request.request_id,
                source=request.source,
            )
            if request.source == "ai":
                idle_id = await db.scalar(
                    select(DesktopVideoAction.id).where(
                        DesktopVideoAction.set_id == row.id,
                        DesktopVideoAction.key == "idle",
                    ),
                )
            await db.commit()
        else:
            original = DesktopVisualSnapshot.model_validate_json(row.context_json)
            if (original.scene_id, original.outfit_id, row.source) != (
                request.scene_id,
                request.outfit_id,
                request.source,
            ):
                raise DesktopVideoStateError("这个创建请求已经用于另一套画面")
        set_id = row.id
    if idle_id is not None:
        try:
            await generate_desktop_image(user_id, idle_id)
        except DesktopVideoError as exc:
            await _record_preparation_error(user_id, idle_id, str(exc))
    async with SESSION_LOCAL() as db:
        row = await _suite(db, user_id, set_id)
        state = await desktop_state(db, user_id)
        return await desktop_set_response(db, row, current_id=state.current_set_id if state else None)


async def rename_desktop_video_set(db: AsyncSession, user_id: int, set_id: int, name: str) -> DesktopVideoSetResponse:
    row = await _suite(db, user_id, set_id)
    row.title = name
    state = await touch_desktop_state(db, user_id)
    await db.commit()
    return await desktop_set_response(db, row, current_id=state.current_set_id)


async def activate_desktop_video_set(db: AsyncSession, user_id: int, set_id: int) -> DesktopVideoStateResponse:
    await _suite(db, user_id, set_id)
    state = await desktop_state(db, user_id, create=True)
    idle = await db.scalar(
        select(DesktopVideoAction).where(DesktopVideoAction.set_id == set_id, DesktopVideoAction.key == "idle"),
    )
    if idle is None or not desktop_action_response(idle).playable:
        raise DesktopVideoStateError("请先完成这套桌景的安静相伴")
    if state.current_set_id != set_id:
        state.current_set_id = set_id
        state.set_epoch += 1
        state.selected_action_id = state.loop_action_id = idle.id
        state.selected_play_id = None
        state.loop_changed_at = utc_now()
        await touch_desktop_state(db, user_id)
    await db.commit()
    return await get_desktop_video_state(db, user_id)


async def delete_desktop_video_set(db: AsyncSession, user_id: int, set_id: int) -> DesktopVideoStateResponse:
    row = await _suite(db, user_id, set_id)
    state = await desktop_state(db, user_id, create=True)
    actions = list(await db.scalars(select(DesktopVideoAction).where(DesktopVideoAction.set_id == set_id)))
    paths = asset_paths(row.context_json, user_id) | ({row.base_image_path} if row.base_image_path else set())
    removed_tasks = []
    for action in actions:
        removed = desktop_removed_video_task(action)
        if removed is not None:
            removed_tasks.append(removed)
        paths |= asset_paths(action.generation_state_json, user_id) | asset_paths(action.accepted_asset_json, user_id)
    if state.current_set_id == set_id:
        fallback = await db.scalar(
            select(DesktopVideoSet).where(
                DesktopVideoSet.user_id == user_id,
                DesktopVideoSet.is_default.is_(True),
                DesktopVideoSet.id != set_id,
            ),
        )
        idle = (
            await db.scalar(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.set_id == fallback.id,
                    DesktopVideoAction.key == "idle",
                ),
            )
            if fallback
            else None
        )
        state.current_set_id = fallback.id if idle and desktop_action_response(idle).playable else None
        state.selected_action_id = state.loop_action_id = idle.id if state.current_set_id else None
        state.selected_play_id = None
        state.loop_changed_at = utc_now() if state.current_set_id else None
        state.set_epoch += 1
    await db.delete(row)
    await enqueue_asset_cleanup(db, user_id, paths, not_before=utc_now() + timedelta(hours=24))
    await touch_desktop_state(db, user_id)
    await db.commit()
    for action in actions:
        cancel_desktop_video_job(action.id)
    spawn_removed_action_task_cancellation(user_id, removed_tasks)
    return await get_desktop_video_state(db, user_id)


async def _record_preparation_error(user_id: int, action_id: int, error: str) -> None:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        if action.status != "queued" or action.generation_state_json is not None:
            return
        action.status, action.stage, action.error = "failed", "prepare", error
        await touch_desktop_state(db, user_id)
        await db.commit()


async def ensure_current_desktop_videos(
    user_id: int,
    *,
    source: str = "user_requested",
    allow_generation: bool = True,
) -> DesktopVideoStateResponse:
    idle_id = None
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await desktop_state(db, user_id, create=True)
        if not state.default_created:
            try:
                snapshot = await load_desktop_visual_snapshot(db, user_id, initial=True)
            except (DesktopVideoError, VisualReasoningError, OSError):
                return await get_desktop_video_state(db, user_id)
            row = await _create_suite(
                db,
                user_id,
                snapshot,
                name="默认桌景",
                request_id=None,
                source="ai",
                is_default=True,
            )
            state.default_created = True
            if state.current_set_id is None:
                state.current_set_id = row.id
                state.set_epoch += 1
            idle_id = await db.scalar(
                select(DesktopVideoAction.id).where(
                    DesktopVideoAction.set_id == row.id,
                    DesktopVideoAction.key == "idle",
                ),
            )
            await db.commit()
        elif allow_generation:
            idle_id = await db.scalar(
                select(DesktopVideoAction.id)
                .join(DesktopVideoSet)
                .where(
                    DesktopVideoSet.user_id == user_id,
                    DesktopVideoSet.is_default.is_(True),
                    DesktopVideoAction.key == "idle",
                    DesktopVideoAction.status == "queued",
                    DesktopVideoAction.generation_state_json.is_(None),
                ),
            )
    if idle_id is not None and allow_generation:
        try:
            await generate_desktop_image(user_id, idle_id, source=source)
        except DesktopVideoError as exc:
            await _record_preparation_error(user_id, idle_id, str(exc))
    async with SESSION_LOCAL() as db:
        return await get_desktop_video_state(db, user_id)


def _validate_draft(
    row: DesktopVideoSet,
    progress: DesktopVideoProgress | None,
    *,
    draft_id: str | None = None,
    base_version: int | None = None,
) -> None:
    if draft_id == "none" and progress is not None:
        raise DesktopVideoStateError("已有新的制作草稿，请刷新后重试")
    if draft_id is not None and draft_id != "none" and (progress is None or progress.generation_id != draft_id):
        raise DesktopVideoStateError("制作草稿已变化，请刷新后重试")
    if base_version is not None and base_version != row.base_version:
        raise DesktopVideoStateError("基础画面已变化，请刷新后重试")


async def _consume_slot(
    db: AsyncSession,
    user_id: int,
    action: DesktopVideoAction,
    progress: DesktopVideoProgress,
) -> None:
    if progress.paid_slot_consumed:
        return
    require_new_generation_call(user_id)
    try:
        await consume_create_slot(
            db,
            user_id,
            source=progress.source,
            creation_key=f"desktop:{action.id}:{progress.generation_id}",
        )
    except ActionPolicyError as exc:
        raise DesktopVideoStateError(str(exc)) from exc
    progress.paid_slot_consumed = True


async def generate_desktop_image(
    user_id: int,
    action_id: int,
    *,
    feedback: str = "",
    regenerate: bool = False,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    source: str = "user_requested",
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        old = desktop_progress(action)
        _validate_draft(
            row,
            old if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
        )
        if action.continuity == "base" and action.key != "idle":
            raise DesktopVideoStateError("这个动作使用安静相伴的基础图，请修改安静相伴")
        if action.status == "processing" and action.stage != "paused":
            return desktop_action_response(action)
        if old is not None and desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先放弃本次制作再重新开始")
        resume = (
            old is not None
            and old.image_only
            and action.stage in {"paused", "image", "prepare"}
            and not feedback
            and not regenerate
            and desktop_progress_can_resume(old)
        )
        removed_task = None
        if resume:
            progress = old
            progress.source = source
            progress.failure_reason = ""
        else:
            previous = old.pose_path if old and old.pose_path else None
            accepted = desktop_accepted_asset(action)
            previous = previous or (accepted.image_path if accepted else None)
            removed_task = desktop_removed_video_task(action)
            old_paths = asset_paths(action.generation_state_json, user_id)
            action.attempt += 1
            progress = DesktopVideoProgress(
                generation_id=uuid4().hex,
                source=source,
                feedback=feedback,
                action_description=desktop_action_description(action),
                duration_seconds=action.target_duration_seconds,
                continuity=action.continuity,
                base_version=row.base_version + 1 if action.continuity == "base" else None,
                replace_base=action.key == "idle",
                image_only=True,
                previous_pose_path=previous,
                regenerate_image=regenerate,
            )
            await enqueue_asset_cleanup(db, user_id, old_paths - ({previous} if previous else set()))
        await _consume_slot(db, user_id, action, progress)
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error = "processing", "prepare", None
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = desktop_action_response(action)
    if not resume:
        cancel_desktop_video_job(action_id)
        spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    schedule_desktop_video_job(user_id, action_id)
    return response


async def upload_desktop_image(
    user_id: int,
    action_id: int,
    data: bytes,
    *,
    expected_set_id: int | None = None,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        if expected_set_id is not None and expected_set_id != row.id:
            raise DesktopVideoStateError("桌景套装已变化")
        _validate_draft(
            row,
            desktop_progress(action) if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
        )
        if action.continuity == "base" and action.key != "idle":
            raise DesktopVideoStateError("这个动作使用安静相伴的基础图，请上传安静相伴的基础图")
        if action.status == "processing":
            raise DesktopVideoStateError("请先取消当前制作")
        old_json, old_status, old_stage = action.generation_state_json, action.status, action.stage
        action.attempt += 1
        progress = DesktopVideoProgress(
            generation_id=uuid4().hex,
            source="user_requested",
            duration_seconds=action.target_duration_seconds,
            continuity=action.continuity,
            base_version=row.base_version + 1 if action.continuity == "base" else None,
            replace_base=action.key == "idle",
            image_only=True,
            action_description=desktop_action_description(action),
        )
        old = desktop_progress(action)
        removed_task = desktop_removed_video_task(action)
        progress.previous_pose_path = old.pose_path if old else None
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error = "processing", "image_upload", None
        directory = f"desktop/{row.id}/{action.id}/{progress.generation_id}"
        baseline = row.base_version
        await touch_desktop_state(db, user_id)
        await db.commit()
    saved_path = None
    try:
        saved_path = await prepare_uploaded_desktop_image(user_id, data, progress, directory)
        progress.pose_path = saved_path
        async with _lock(user_id), SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id, lock=True)
            row = await _suite(db, user_id, action.set_id)
            current = desktop_progress(action)
            _validate_draft(row, current, draft_id=progress.generation_id, base_version=baseline)
            action.generation_state_json = progress.model_dump_json()
            action.status, action.stage, action.error = "review_pending", "image_review", None
            await enqueue_asset_cleanup(db, user_id, asset_paths(old_json, user_id))
            await touch_desktop_state(db, user_id)
            await db.commit()
            cancel_desktop_video_job(action_id)
            spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
            return desktop_action_response(action)
    except Exception:
        if saved_path:
            await asyncio.to_thread(unlink_companion_asset, saved_path)
        async with SESSION_LOCAL() as db:
            await desktop_state(db, user_id, lock=True)
            action = await db.scalar(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.user_id == user_id,
                    DesktopVideoAction.id == action_id,
                ),
            )
            current = desktop_progress(action) if action else None
            if action is not None and current is not None and current.generation_id == progress.generation_id:
                action.generation_state_json, action.status, action.stage = old_json, old_status, old_stage
                action.error = "图片上传未完成，请重试"
                await touch_desktop_state(db, user_id)
                await db.commit()
        raise


async def confirm_desktop_image(
    user_id: int,
    action_id: int,
    *,
    draft_id: str,
    expected_base_version: int | None = None,
    generate_video: bool = True,
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        progress = desktop_progress(action)
        _validate_draft(row, progress, draft_id=draft_id, base_version=expected_base_version)
        if progress is None or not progress.pose_path:
            raise DesktopVideoStateError("请先准备基础图片")
        if desktop_progress_has_unknown_submission(progress):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先放弃本次制作再重新开始")
        if progress.image_confirmed and action.stage not in {"image_review", "image_confirmed"}:
            return desktop_action_response(action)
        if action.stage not in {"image_review", "image_confirmed"}:
            raise DesktopVideoStateError("当前图片尚未准备好")
        progress.image_confirmed = True
        progress.image_only = False
        progress.source = "user_requested"
        progress.motion_prompt = ""
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error = (
            ("processing", "video", None) if generate_video else ("review_pending", "image_confirmed", None)
        )
        if generate_video:
            await _consume_slot(db, user_id, action, progress)
        action.generation_state_json = progress.model_dump_json()
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = desktop_action_response(action)
    if generate_video:
        schedule_desktop_video_job(user_id, action_id)
    return response


async def cancel_desktop_action(
    user_id: int,
    action_id: int,
    *,
    draft_id: str | None = None,
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        _validate_draft(row, desktop_progress(action), draft_id=draft_id)
        removed_task = desktop_removed_video_task(action)
        await enqueue_asset_cleanup(db, user_id, asset_paths(action.generation_state_json, user_id))
        action.generation_state_json = None
        action.status = "ready" if desktop_accepted_asset(action) else "queued"
        action.stage, action.error, action.update_pending = (
            "complete" if action.accepted_asset_json else "prepare",
            None,
            False,
        )
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = desktop_action_response(action)
    cancel_desktop_video_job(action_id)
    spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    return response


async def schedule_pending_desktop_updates(user_id: int, set_id: int) -> None:
    async with SESSION_LOCAL() as db:
        actions = list(
            await db.scalars(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.user_id == user_id,
                    DesktopVideoAction.set_id == set_id,
                    DesktopVideoAction.update_pending.is_(True),
                    DesktopVideoAction.enabled.is_(True),
                ),
            ),
        )
        ids = [action.id for action in actions]
        invalidated = list(
            await db.scalars(
                select(DesktopVideoAction.id).where(
                    DesktopVideoAction.user_id == user_id,
                    DesktopVideoAction.set_id == set_id,
                    DesktopVideoAction.key != "idle",
                    DesktopVideoAction.continuity == "base",
                    DesktopVideoAction.generation_state_json.is_(None),
                ),
            ),
        )
    for invalidated_id in invalidated:
        cancel_desktop_video_job(invalidated_id)
    if invalidated:
        await asyncio.sleep(0)
    for action_id in ids:
        async with SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id, lock=True)
            if not action.enabled or not action.update_pending:
                continue
            row = await _suite(db, user_id, action.set_id)
            baseline = row.base_version
        try:
            await generate_desktop_action(
                user_id,
                action_id,
                expected_draft_id="none",
                expected_base_version=baseline,
                pending_update=True,
            )
        except DesktopVideoError as exc:
            async with SESSION_LOCAL() as db:
                action = await desktop_action(db, user_id, action_id, lock=True)
                row = await _suite(db, user_id, action.set_id)
                if row.base_version != baseline or action.generation_state_json or not action.update_pending:
                    continue
                action.update_pending = False
                action.error = str(exc)
                await touch_desktop_state(db, user_id)
                await db.commit()


async def generate_desktop_action(
    user_id: int,
    action_id: int,
    *,
    feedback: str = "",
    source: str = "user_requested",
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    pending_update: bool = False,
) -> DesktopVideoActionResponse:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        if pending_update and (not action.enabled or not action.update_pending):
            return desktop_action_response(action)
        progress = desktop_progress(action)
        _validate_draft(
            row,
            progress if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
        )
        if action.status == "processing" and action.stage != "paused":
            return desktop_action_response(action)
        if progress is not None and desktop_progress_has_unknown_submission(progress):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先等待结果或放弃本次制作")
        if progress is not None and action.stage == "image_review":
            raise DesktopVideoStateError("请先确认图片后再生成视频")
        accepted = desktop_accepted_asset(action)
        image = row.base_image_path if action.continuity == "base" else accepted.image_path if accepted else None
        if progress is not None and progress.image_confirmed and progress.pose_path and action.stage != "complete":
            image = progress.pose_path
        needs_image = not image or (progress is not None and progress.image_only and action.stage != "complete")
        if action.continuity == "base" and action.key != "idle" and not row.base_image_path:
            raise DesktopVideoStateError("请先制作安静相伴")
        kind, duration, continuity = action.kind, action.target_duration_seconds, action.continuity
        resumable = (
            progress is not None
            and progress.image_confirmed
            and action.stage not in {"complete", "review"}
            and not feedback
            and desktop_progress_can_resume(progress)
        )
        frozen_chain = progress.video_chain_json if resumable else None
        observed_draft = progress.generation_id if progress is not None and action.stage != "complete" else "none"
        observed_base = row.base_version
    if needs_image:
        return await generate_desktop_image(
            user_id,
            action_id,
            feedback=feedback,
            expected_draft_id=expected_draft_id,
            expected_base_version=expected_base_version,
            source=source,
        )
    seconds, providers = (
        (duration, [])
        if resumable and frozen_chain
        else await resolve_desktop_video_plan(
            user_id,
            kind=kind,
            duration_seconds=duration,
            continuity=continuity,
        )
    )
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        state = await desktop_state(db, user_id, create=True)
        old = desktop_progress(action)
        if pending_update and (not action.enabled or not action.update_pending):
            return desktop_action_response(action)
        _validate_draft(
            row,
            old if action.stage != "complete" else None,
            draft_id=observed_draft,
            base_version=observed_base,
        )
        _validate_draft(
            row,
            old if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
        )
        if source == "autonomous" and (not state.autonomous_enabled or not SETTINGS.action_autocreate_enabled):
            raise DesktopVideoStateError("自主制作动作当前已关闭")
        if action.status == "processing" and action.stage != "paused":
            return desktop_action_response(action)
        if old is not None and desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先放弃本次制作再重新开始")
        if action.stage == "image_review":
            raise DesktopVideoStateError("请先确认图片后再生成视频")
        resume = (
            old is not None
            and old.image_confirmed
            and action.stage not in {"complete", "review"}
            and not feedback
            and desktop_progress_can_resume(old)
        )
        accepted = desktop_accepted_asset(action)
        confirmed_draft = (
            old if old is not None and old.image_confirmed and old.pose_path and action.stage != "complete" else None
        )
        image = (
            confirmed_draft.pose_path
            if confirmed_draft
            else row.base_image_path
            if continuity == "base"
            else accepted.image_path
            if accepted
            else None
        )
        if not image:
            raise DesktopVideoStateError("基础图片已变化，请刷新后重试")
        if resume and old.continuity == "base" and action.key != "idle" and old.base_version != row.base_version:
            raise DesktopVideoStateError("基础画面已变化，请重新制作这个动作")
        removed_task = None
        if resume:
            progress = old
            progress.source = source
            progress.image_only = False
            progress.failure_reason = ""
        else:
            removed_task = desktop_removed_video_task(action)
            old_paths = asset_paths(action.generation_state_json, user_id)
            action.attempt += 1
            progress = DesktopVideoProgress(
                generation_id=uuid4().hex,
                source=source,
                feedback=feedback,
                action_description=desktop_action_description(action),
                duration_seconds=seconds,
                continuity=continuity,
                base_image_path=confirmed_draft.base_image_path
                if confirmed_draft
                else row.base_image_path
                if continuity == "base"
                else None,
                base_version=confirmed_draft.base_version
                if confirmed_draft
                else row.base_version
                if continuity == "base"
                else None,
                replace_base=confirmed_draft.replace_base if confirmed_draft else False,
                aspect_ratio=confirmed_draft.aspect_ratio if confirmed_draft else "16:9",
                frame_width=confirmed_draft.frame_width if confirmed_draft else None,
                frame_height=confirmed_draft.frame_height if confirmed_draft else None,
                pose_path=image,
                image_confirmed=True,
                force_review=action.needs_update,
            )
        if not progress.video_chain_json:
            progress.duration_seconds = seconds
            progress.video_chain_json = json.dumps(
                {"providers": [provider.model_dump(mode="json") for provider in providers]},
            )
        if not resume:
            await enqueue_asset_cleanup(db, user_id, old_paths - asset_paths(progress.model_dump_json(), user_id))
        await _consume_slot(db, user_id, action, progress)
        action.duration_seconds = progress.duration_seconds
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error, action.update_pending = "processing", "video", None, False
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = desktop_action_response(action)
    if not resume:
        cancel_desktop_video_job(action_id)
        spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    schedule_desktop_video_job(user_id, action_id)
    return response


async def upload_desktop_action(
    user_id: int,
    action_id: int,
    data: bytes,
    extension: str,
    *,
    expected_set_id: int | None = None,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
) -> DesktopVideoActionResponse:
    """保存用户原视频，无转码检查后按当前成品和候选的采用规则处理。"""
    if not data:
        raise DesktopVideoStateError("上传的视频文件为空")
    extension = extension.lower().lstrip(".")
    detected = sniff_media_ext(data)
    if extension not in {"mp4", "mov"} or detected not in {"mp4", "mov"}:
        raise DesktopVideoStateError("仅支持 MP4 或 MOV 视频")
    async with _lock(user_id), SESSION_LOCAL() as db:
        initial = await desktop_action(db, user_id, action_id, lock=True)
        if expected_set_id is not None and expected_set_id != initial.set_id:
            raise DesktopVideoStateError("桌面生活组合已经变化")
        if initial.status == "processing":
            raise DesktopVideoStateError("这个桌面动作正在制作，请等待当前任务完成")
        row = await _suite(db, user_id, initial.set_id)
        if initial.continuity == "base" and initial.key != "idle" and not row.base_image_path:
            raise DesktopVideoStateError("请先制作安静相伴")
        _validate_draft(
            row,
            desktop_progress(initial) if initial.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
        )
        initial.attempt += 1
        generation_id = uuid4().hex
        directory = f"desktop/{initial.set_id}/{initial.id}/{generation_id}"
        old_paths = asset_paths(initial.generation_state_json, user_id)
        removed_task = desktop_removed_video_task(initial)
        previous = desktop_accepted_asset(initial)
        if previous is not None:
            old_paths -= {previous.video_path, previous.poster_path}
        progress = DesktopVideoProgress(
            generation_id=generation_id,
            source="external_upload",
            duration_seconds=initial.duration_seconds,
            continuity=initial.continuity,
            base_version=(row.base_version + 1 if initial.key == "idle" else row.base_version)
            if initial.continuity == "base"
            else None,
            base_image_path=row.base_image_path if initial.continuity == "base" and initial.key != "idle" else None,
            replace_base=initial.key == "idle",
            video_chain_json="{}",
        )
        await enqueue_asset_cleanup(db, user_id, old_paths)
        initial.generation_state_json = progress.model_dump_json()
        initial.status, initial.stage, initial.error = "processing", "upload", None
        state = await desktop_state(db, user_id)
        if state is not None:
            state.preparation_error = None
        await touch_desktop_state(db, user_id)
        await db.commit()
    cancel_desktop_video_job(action_id)
    spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    source_path: str | None = None
    try:
        source_path = await save_action_source_asset_async(
            data,
            user_id=user_id,
            generation_id=generation_id,
            attempt=initial.attempt,
            ext=detected,
            directory=directory,
        )
        async with SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id, lock=True)
            current = desktop_progress(action)
            if current is None or current.generation_id != generation_id or action.status != "processing":
                raise DesktopVideoStateError("上传任务已经失效")
            current.source_path = source_path
            action.generation_state_json = current.model_dump_json()
            action.stage = "process"
            await touch_desktop_state(db, user_id)
            await db.commit()
            response = desktop_action_response(action)
    except Exception as exc:
        if source_path:
            await asyncio.to_thread(unlink_companion_asset, source_path)
        async with SESSION_LOCAL() as db:
            await desktop_state(db, user_id, lock=True)
            action = await db.scalar(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.id == action_id,
                    DesktopVideoAction.user_id == user_id,
                ),
            )
            current = desktop_progress(action) if action else None
            if action is not None and current is not None and current.generation_id == generation_id:
                current.failure_reason = "failed"
                action.generation_state_json = current.model_dump_json()
                action.status, action.stage, action.error = "failed", "upload", "上传视频保存失败，请重试"
                await touch_desktop_state(db, user_id)
                await db.commit()
        if isinstance(exc, DesktopVideoError):
            raise
        raise DesktopVideoStateError("上传视频保存失败，请重试") from exc
    schedule_desktop_video_job(user_id, action_id)
    return response


async def review_desktop_action(
    user_id: int,
    action_id: int,
    *,
    accepted: bool,
    draft_id: str | None = None,
    expected_base_version: int | None = None,
) -> DesktopVideoActionResponse:
    removed_tasks: list[RemovedActionVideoTask] = []
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        progress = desktop_progress(action)
        row = await _suite(db, user_id, action.set_id)
        if action.stage == "complete" and progress and draft_id == progress.generation_id:
            return desktop_action_response(action)
        _validate_draft(row, progress, draft_id=draft_id, base_version=expected_base_version)
        if action.status != "review_pending" or action.stage == "image_review":
            raise DesktopVideoStateError("这个画面当前没有待采用视频")
        if progress is None or progress.candidate is None:
            raise DesktopVideoStateError("待确认成品缺失，请重做")
        if accepted:
            removed_tasks = await publish_desktop_asset(db, action, progress.candidate)
            presentation = get_presentation_snapshot(user_id)
            if presentation is not None and presentation.mode == "desktop":
                await fulfill_desktop_play_intents(
                    db,
                    action,
                    presentation_revision=presentation.revision,
                    allow_autonomous=get_user_proactive_record(user_id).available
                    and await get_disturbance_tier(user_id, db=db) == "autonomous",
                )
        else:
            await enqueue_asset_cleanup(db, user_id, {progress.candidate.video_path, progress.candidate.poster_path})
            await enqueue_asset_cleanup(db, user_id, asset_paths(action.generation_state_json, user_id))
            action.generation_state_json = None
            action.status = "ready" if action.accepted_asset_json else "queued"
            action.stage, action.error = "complete" if action.accepted_asset_json else "prepare", None
            await touch_desktop_state(db, user_id)
        await db.commit()
        response = desktop_action_response(action)
        set_id = action.set_id
    if accepted:
        spawn_removed_action_task_cancellation(user_id, removed_tasks)
        await schedule_pending_desktop_updates(user_id, set_id)
    return response


async def set_desktop_video_preferences(
    db: AsyncSession,
    user_id: int,
    request: DesktopVideoPreferences,
) -> DesktopVideoStateResponse:
    await desktop_preferences(db, user_id, request)
    return await get_desktop_video_state(db, user_id)


async def play_desktop_action(
    db: AsyncSession,
    user_id: int,
    request: DesktopVideoPlayRequest,
    *,
    source: str = "user_requested",
) -> DesktopVideoPlayCommand:
    if source == "user_requested":
        await _wait_desktop_mode(user_id)
    else:
        _require_desktop_mode(user_id)
    action = await desktop_action(db, user_id, request.action_id, lock=True)
    if request.expected_set_id is not None and request.expected_set_id != action.set_id:
        raise DesktopVideoStateError("桌面动作组合已经变化")
    state = await require_desktop_current(db, user_id, action.set_id)
    if action.needs_update:
        raise DesktopVideoStateError("基础画面已更新，请先更新这个动作")
    if source in {"autonomous", "chat_expression"} and (state.pinned or not state.autonomous_enabled):
        raise DesktopVideoStateError("桌面动作已固定或自主切换已关闭")
    if source == "autonomous":
        if not get_user_proactive_record(user_id).available:
            raise DesktopVideoStateError("当前不能自主切换桌面动作")
        if await get_disturbance_tier(user_id, db=db) != "autonomous":
            raise DesktopVideoStateError("当前打扰档位不允许自主表演")
    _require_desktop_mode(user_id)
    presentation = get_presentation_snapshot(user_id)
    if desktop_accepted_asset(action) is None:
        progress = desktop_progress(action)
        if action.status not in {"queued", "processing", "review_pending"} and not (
            action.status == "failed" and progress is not None and progress.failure_reason == "temporary"
        ):
            raise DesktopVideoStateError("这个桌面动作尚未就绪")
        playback = DesktopVideoPlayback(
            user_id=user_id,
            set_id=action.set_id,
            action_id=action.id,
            play_id=str(uuid4()),
            set_epoch=state.set_epoch,
            presentation_revision=presentation.revision,
            status="waiting",
            source=source,
            expires_at=utc_now() + timedelta(seconds=DEFERRED_PLAY_INTENT_TTL_SECONDS),
        )
        db.add(playback)
        return DesktopVideoPlayCommand(
            play_id=playback.play_id,
            set_id=action.set_id,
            action_id=action.id,
            set_epoch=state.set_epoch,
            kind=action.kind,
            expires_at=playback.expires_at,
            video_url="",
            poster_url="",
            version=action.version,
            continuity="independent",
            base_version=None,
        )
    return await emit_desktop_play(db, user_id, action, source=source, presentation_revision=presentation.revision)


async def claim_desktop_playback(db: AsyncSession, user_id: int, play_id: str, client_id: str) -> bool:
    _require_desktop_mode(user_id)
    presentation = get_presentation_snapshot(user_id)
    return await claim_desktop_play(
        db,
        user_id,
        play_id,
        client_id,
        presentation_revision=presentation.revision,
        allow_autonomous=get_user_proactive_record(user_id).available
        and await get_disturbance_tier(user_id, db=db) == "autonomous",
    )


async def record_desktop_playback(db: AsyncSession, user_id: int, play_id: str, receipt: DesktopVideoReceipt) -> None:
    presentation = get_presentation_snapshot(user_id)
    await record_desktop_receipt(
        db,
        user_id,
        play_id,
        receipt,
        presentation_revision=presentation.revision
        if presentation is not None and presentation.mode == "desktop"
        else -1,
    )


class DesktopProposalVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(approve|reuse|defer|reject)$")
    reason: str = Field(max_length=400)
    reuse_action_id: int | None = None


async def design_desktop_action(
    user_id: int,
    request: DesktopVideoDesignRequest,
    *,
    source: str = "user_requested",
    require_desktop: bool = False,
) -> DesktopVideoProposalResponse:
    retry_action_id: int | None = None
    async with _lock(user_id), SESSION_LOCAL() as db:
        if require_desktop:
            _require_desktop_mode(user_id)
        state = await desktop_state(db, user_id, lock=True)
        if state is None or (state.current_set_id is None and request.expected_set_id is None):
            raise DesktopVideoStateError("请先准备可用画面")
        if source == "autonomous" and (not state.autonomous_enabled or not SETTINGS.action_autocreate_enabled):
            raise DesktopVideoStateError("自主制作桌面动作当前已关闭")
        target_id = request.expected_set_id or state.current_set_id
        row = await _suite(db, user_id, target_id)
        if source == "autonomous" and row.id != state.current_set_id:
            raise DesktopVideoStateError("只能为当前显示的画面自主制作动作")
        normalized = normalize_action_name(request.name)
        key = "custom_" + hashlib.sha256(normalized.encode()).hexdigest()[:24]
        existing_action = await db.scalar(
            select(DesktopVideoAction).where(DesktopVideoAction.set_id == row.id, DesktopVideoAction.key == key),
        )
        if existing_action is not None:
            proposal = await db.scalar(
                select(DesktopVideoProposal)
                .where(
                    DesktopVideoProposal.user_id == user_id,
                    DesktopVideoProposal.set_id == row.id,
                    DesktopVideoProposal.action_id == existing_action.id,
                )
                .order_by(DesktopVideoProposal.id.desc())
                .limit(1),
            )
            response = DesktopVideoProposalResponse(
                proposal_id=proposal.id if proposal else 0,
                set_id=row.id,
                action_id=existing_action.id,
                status=existing_action.status,
                review_reason="同名动作沿用原设计，修改内容请使用新的动作名称",
            )
            if desktop_accepted_asset(existing_action) is not None and not existing_action.needs_update:
                response.status = "reused"
                return response
            if (
                not existing_action.needs_update
                and existing_action.status not in {"queued", "failed"}
                and existing_action.stage != "paused"
            ):
                return response
            durations = {existing_action.duration_seconds}
            if proposal is not None:
                original = DesktopVideoDesignRequest.model_validate_json(proposal.design_json)
                durations.add(original.duration_seconds)
            if (
                request.motion_description != existing_action.description
                or request.kind != existing_action.kind
                or request.duration_seconds not in durations
                or request.use_when != json.loads(existing_action.use_when_json)
                or request.avoid_when != json.loads(existing_action.avoid_when_json)
                or request.continuity != existing_action.continuity
                or request.allowed_triggers != json.loads(existing_action.allowed_triggers_json)
            ):
                return response
            retry_action_id = existing_action.id
        else:
            semantic_design = request.model_dump(exclude={"reason", "expected_set_id"})
            semantic_design["name"] = normalized
            semantic_design["allowed_triggers"] = sorted(request.allowed_triggers)
            fingerprint = hashlib.sha256(
                json.dumps(
                    semantic_design,
                    sort_keys=True,
                ).encode(),
            ).hexdigest()
            proposal = await db.scalar(
                select(DesktopVideoProposal).where(
                    DesktopVideoProposal.set_id == row.id,
                    DesktopVideoProposal.fingerprint == fingerprint,
                ),
            )
            if proposal is None:
                proposal = DesktopVideoProposal(
                    user_id=user_id,
                    set_id=row.id,
                    fingerprint=fingerprint,
                    design_json=request.model_dump_json(),
                    source=source,
                    status="pending",
                    review_reason="",
                )
                db.add(proposal)
                await db.flush()
                await touch_desktop_state(db, user_id)
            elif proposal.status in {"rejected", "deferred"}:
                if proposal.status == "rejected":
                    return DesktopVideoProposalResponse(
                        proposal_id=proposal.id,
                        set_id=row.id,
                        action_id=proposal.action_id,
                        status=proposal.status,
                        review_reason=proposal.review_reason,
                    )
                proposal.status, proposal.source = "pending", source
            await db.commit()
            response = DesktopVideoProposalResponse(
                proposal_id=proposal.id,
                set_id=row.id,
                action_id=proposal.action_id,
                status=proposal.status,
                review_reason=proposal.review_reason,
            )
    if retry_action_id is not None:
        try:
            action = await generate_desktop_action(user_id, retry_action_id, source=source)
        except (DesktopVideoError, ActionPolicyError, LlmCallBlockedError, VisualReasoningError) as exc:
            response.status, response.review_reason = "deferred", str(exc)
            return response
        response.status = action.status
        if response.proposal_id:
            async with _lock(user_id), SESSION_LOCAL() as db:
                proposal = await db.scalar(
                    select(DesktopVideoProposal).where(
                        DesktopVideoProposal.id == response.proposal_id,
                        DesktopVideoProposal.user_id == user_id,
                        DesktopVideoProposal.action_id == retry_action_id,
                    ),
                )
                if proposal is not None and proposal.status == "deferred":
                    proposal.status = "approved"
                    await touch_desktop_state(db, user_id)
                    await db.commit()
        return response
    _schedule_desktop_review(user_id, response.proposal_id)
    return response


async def _review_desktop_proposal(user_id: int, proposal_id: int) -> None:
    try:
        async with SESSION_LOCAL() as db:
            proposal = await db.scalar(
                select(DesktopVideoProposal).where(
                    DesktopVideoProposal.id == proposal_id,
                    DesktopVideoProposal.user_id == user_id,
                ),
            )
            if proposal is None or proposal.status != "pending":
                return
            row = await db.get(DesktopVideoSet, proposal.set_id)
            snapshot = DesktopVisualSnapshot.model_validate_json(row.context_json)
            reviewed_base_version, reviewed_base_path = row.base_version, row.base_image_path
            portrait_path = row.base_image_path or snapshot.portrait_path
            request = DesktopVideoDesignRequest.model_validate_json(proposal.design_json)
            language = resolve_language(await get_user_setting(db, user_id, "language"))
            existing = list(
                await db.scalars(
                    select(DesktopVideoAction).where(
                        DesktopVideoAction.set_id == row.id,
                        DesktopVideoAction.enabled.is_(True),
                        DesktopVideoAction.accepted_asset_json.is_not(None),
                        DesktopVideoAction.needs_update.is_(False),
                    ),
                ),
            )
            existing_payload = [
                {
                    "id": action.id,
                    "name": action.name,
                    "description": desktop_action_description(action),
                    "kind": action.kind,
                    "duration_seconds": action.duration_seconds,
                    "use_when": json.loads(action.use_when_json),
                    "avoid_when": json.loads(action.avoid_when_json),
                    "continuity": asset.continuity,
                    "allowed_triggers": json.loads(action.allowed_triggers_json),
                }
                for action in existing
                if (asset := desktop_accepted_asset(action)) is not None
            ]

        async def before_submit() -> None:
            require_new_generation_call(user_id)
            async with SESSION_LOCAL() as db:
                current_suite = await _suite(db, user_id, proposal.set_id)
                if (current_suite.base_version, current_suite.base_image_path) != (
                    reviewed_base_version,
                    reviewed_base_path,
                ):
                    raise LlmCallBlockedError("基础画面已更新，请基于新画面重新评审动作")
                state = await desktop_state(db, user_id, create=True)
                current_proposal = await db.get(DesktopVideoProposal, proposal_id)
                if current_proposal is None or current_proposal.status != "pending":
                    raise LlmCallBlockedError("这项动作设计已停止评审")
                if proposal.source == "autonomous" and (
                    not state.autonomous_enabled
                    or not SETTINGS.action_autocreate_enabled
                    or state.current_set_id != proposal.set_id
                    or get_presentation_snapshot(user_id) is None
                    or get_presentation_snapshot(user_id).mode != "desktop"
                ):
                    raise LlmCallBlockedError("自主制作当前已关闭")

        portrait = await asyncio.to_thread(read_valid_image_data_uri, portrait_path)
        outfit = await asyncio.to_thread(read_valid_image_data_uri, row.base_image_path or snapshot.outfit_path)
        if not portrait or not outfit:
            raise DesktopVideoStateError("参考图片无法读取")
        raw = await vision_chat(
            user_id,
            DESKTOP_ACTION_REVIEW,
            json.dumps(
                {
                    "design": request.model_dump(exclude={"expected_set_id"}),
                    "output_language": language,
                    "identity": snapshot.identity.features.model_dump() if not row.base_image_path else {},
                    "persona": snapshot.persona,
                    "outfit": snapshot.outfit_description if not row.base_image_path else "",
                    "environment": snapshot.scene_description
                    if not row.base_image_path
                    else "以已确认图片中的环境为准",
                    "reference_mode": "confirmed" if row.base_image_path else "sources",
                    "existing_actions": existing_payload,
                },
                ensure_ascii=False,
            ),
            reference_images=(portrait,) if row.base_image_path else (portrait, outfit),
            before_submit=before_submit,
            task=AiTaskKey.desktop_life,
        )
        verdict = DesktopProposalVerdict.model_validate(parse_llm_json(raw))
        if verdict.decision == "reuse" and verdict.reuse_action_id not in {action["id"] for action in existing_payload}:
            raise DesktopVideoStateError("评审复用的动作不属于当前可用列表")
        if verdict.decision != "reuse" and verdict.reuse_action_id is not None:
            raise DesktopVideoStateError("评审结果包含不适用的复用动作")
        action_id: int | None = None
        async with _lock(user_id), SESSION_LOCAL() as db:
            state = await desktop_state(db, user_id, create=True)
            current_proposal = await db.get(DesktopVideoProposal, proposal_id)
            if current_proposal is None or current_proposal.status != "pending":
                return
            current_suite = await _suite(db, user_id, current_proposal.set_id)
            if (current_suite.base_version, current_suite.base_image_path) != (
                reviewed_base_version,
                reviewed_base_path,
            ):
                current_proposal.status = "deferred"
                current_proposal.review_reason = "基础画面已更新，请基于新画面重新评审动作"
                await touch_desktop_state(db, user_id)
                await db.commit()
                return
            if current_proposal.source == "autonomous" and (
                not state.autonomous_enabled
                or not SETTINGS.action_autocreate_enabled
                or state.current_set_id != current_proposal.set_id
                or get_presentation_snapshot(user_id) is None
                or get_presentation_snapshot(user_id).mode != "desktop"
            ):
                current_proposal.status = "deferred"
                current_proposal.review_reason = "当前已停止自主制作，请手动重试"
                await touch_desktop_state(db, user_id)
                await db.commit()
                return
            current_proposal.review_reason = verdict.reason
            if verdict.decision == "approve":
                key = "custom_" + hashlib.sha256(normalize_action_name(request.name).encode()).hexdigest()[:24]
                action = await db.scalar(
                    select(DesktopVideoAction).where(
                        DesktopVideoAction.set_id == current_proposal.set_id,
                        DesktopVideoAction.key == key,
                    ),
                )
                if action is None:
                    action = DesktopVideoAction(
                        user_id=user_id,
                        set_id=current_proposal.set_id,
                        key=key,
                        name=request.name,
                        description=request.motion_description,
                        kind=request.kind,
                        duration_seconds=request.duration_seconds,
                        target_duration_seconds=request.duration_seconds,
                        continuity=request.continuity,
                        allowed_triggers_json=json.dumps(request.allowed_triggers),
                        preset=False,
                        use_when_json=json.dumps(request.use_when, ensure_ascii=False),
                        avoid_when_json=json.dumps(request.avoid_when, ensure_ascii=False),
                        enabled=True,
                        status="queued",
                        stage="prepare",
                        attempt=0,
                        version=0,
                    )
                    db.add(action)
                    await db.flush()
                current_proposal.action_id = action_id = action.id
                current_proposal.status = "approved"
            elif verdict.decision == "reuse":
                current_proposal.action_id = verdict.reuse_action_id
                current_proposal.status = "reused"
            else:
                current_proposal.status = "deferred" if verdict.decision == "defer" else "rejected"
            await touch_desktop_state(db, user_id)
            await db.commit()
        if action_id is not None:
            await generate_desktop_action(user_id, action_id, source=proposal.source)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Desktop action proposal deferred", extra={"proposal_id": proposal_id}, exc_info=True)
        async with SESSION_LOCAL() as db:
            await desktop_state(db, user_id, lock=True)
            row = await db.scalar(
                select(DesktopVideoProposal).where(
                    DesktopVideoProposal.id == proposal_id,
                    DesktopVideoProposal.user_id == user_id,
                ),
            )
            if row is not None and row.status == "pending":
                row.status = "deferred"
                row.review_reason = str(exc) if isinstance(exc, DesktopVideoError) else "评审或制作暂未完成，可稍后重试"
                await touch_desktop_state(db, user_id)
                await db.commit()


def _schedule_desktop_review(user_id: int, proposal_id: int) -> None:
    if not proposal_id or proposal_id in _REVIEWS:
        return
    task = asyncio.create_task(
        _review_desktop_proposal(user_id, proposal_id),
        name=f"desktop-review-{proposal_id}",
    )
    _REVIEWS[proposal_id] = task
    track_user_task(user_id, task)
    task.add_done_callback(lambda _task: _REVIEWS.pop(proposal_id, None))


async def build_desktop_action_context(db: AsyncSession, user_id: int, *, language: str) -> str:
    state = await get_desktop_video_state(db, user_id)
    actions = state.current.actions if state.current else []
    payload = {
        "pinned": state.pinned,
        "autonomous_enabled": state.autonomous_enabled,
        "current_action_id": state.selected_action_id,
        "preparation_error": state.preparation_error,
        "ready_actions": [
            {
                "id": action.id,
                "name": action.name,
                "description": action.description,
                "kind": action.kind,
                "duration_seconds": action.duration_seconds,
                "use_when": action.use_when,
                "avoid_when": action.avoid_when,
                "allowed_triggers": action.allowed_triggers,
                "continuity": action.continuity,
            }
            for action in actions
            if action.playable
        ],
        "preparing": [
            {
                "action_id": action.id,
                "name": action.name,
                "status": action.status,
                "stage": action.stage,
                "error": action.error,
            }
            for action in actions
            if action.status != "ready"
        ],
    }
    proposals = list(
        await db.scalars(
            select(DesktopVideoProposal)
            .where(
                DesktopVideoProposal.user_id == user_id,
                DesktopVideoProposal.set_id == (state.current.id if state.current else 0),
            )
            .order_by(DesktopVideoProposal.updated_at.desc())
            .limit(5),
        ),
    )
    payload["proposals"] = [
        {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "action_id": proposal.action_id,
            "reason": proposal.review_reason,
        }
        for proposal in proposals
    ]
    return resolve_prompt_text(DESKTOP_CONTEXT_GUIDANCE, language) + "\n" + json.dumps(payload, ensure_ascii=False)


class DesktopIdleChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: int | None = None
    reason: str = Field(default="", max_length=400)


async def choose_idle_desktop_action(
    user_id: int,
    idle_seconds: float,
    local_hour: int,
    *,
    trigger: Literal["periodic", "user_return"] = "periodic",
) -> dict[str, object]:
    presentation = get_presentation_snapshot(user_id)
    record = get_user_proactive_record(user_id)
    contact_revision = record.contact_revision
    if presentation is None or presentation.mode != "desktop" or not record.available or record.busy_count:
        return {"expressed": False, "action_id": None, "reason": "unavailable"}
    async with SESSION_LOCAL() as db:
        state = await get_desktop_video_state(db, user_id)
        persisted_state = await desktop_state(db, user_id)
        if (
            state.current is None
            or persisted_state is None
            or state.pinned
            or not state.autonomous_enabled
            or await get_disturbance_tier(user_id, db=db) != "autonomous"
        ):
            return {"expressed": False, "action_id": None, "reason": "disabled"}
        recent_context = await load_recent_context_window(db, user_id)
        desktop_environment = await activated_desktop_environment(db, user_id)
        actions: list[dict[str, object]] = []
        for action in state.current.actions:
            if (
                not action.enabled
                or not action.playable
                or action.id == state.selected_action_id
                or trigger not in action.allowed_triggers
            ):
                continue
            stored_action = await desktop_action(db, user_id, action.id)
            asset = desktop_accepted_asset(stored_action)
            if (
                asset is None
                or await desktop_autonomous_block_reason(
                    db,
                    persisted_state,
                    stored_action,
                    asset,
                    presentation_revision=presentation.revision,
                )
                is not None
            ):
                continue
            actions.append(
                {
                    "id": action.id,
                    "name": action.name,
                    "description": action.description,
                    "kind": action.kind,
                    "use_when": action.use_when,
                    "avoid_when": action.avoid_when,
                    "allowed_triggers": action.allowed_triggers,
                    "continuity": action.continuity,
                    "duration_seconds": action.duration_seconds,
                },
            )
    if not actions:
        return {"expressed": False, "action_id": None, "reason": "not_ready"}
    context = await load_companion_prompt_context(user_id)
    if context is None:
        return {"expressed": False, "action_id": None, "reason": "not_ready"}
    raw = await chat(
        user_id,
        DESKTOP_IDLE_EXPRESSION,
        json.dumps(
            {
                "persona": context.persona_extras,
                "output_language": context.language,
                "current_mood": context.current_mood,
                "current_time": context.current_time,
                "long_term_memories": context.memories_block,
                "recent_context": recent_context,
                "environment": {
                    "current": desktop_environment.get("current"),
                    "action": desktop_environment.get("action"),
                },
                "idle_seconds": idle_seconds,
                "trigger": trigger,
                "local_hour": local_hour,
                "current_action_id": state.selected_action_id,
                "actions": actions,
            },
            ensure_ascii=False,
        ),
        task=AiTaskKey.proactive,
    )
    choice = DesktopIdleChoice.model_validate(parse_llm_json(raw))
    if (
        choice.action_id is None
        or choice.action_id == state.selected_action_id
        or choice.action_id not in {action["id"] for action in actions}
        or presentation != get_presentation_snapshot(user_id)
        or contact_revision != get_user_proactive_record(user_id).contact_revision
        or not get_user_proactive_record(user_id).available
        or get_user_proactive_record(user_id).busy_count
        or await get_disturbance_tier(user_id) != "autonomous"
    ):
        return {"expressed": False, "action_id": None, "reason": choice.reason or "keep_current"}
    async with SESSION_LOCAL() as db:
        current_state = await desktop_state(db, user_id, lock=True)
        if (
            current_state is None
            or current_state.version != state.version
            or current_state.current_set_id != state.current.id
            or presentation != get_presentation_snapshot(user_id)
            or contact_revision != get_user_proactive_record(user_id).contact_revision
            or not get_user_proactive_record(user_id).available
            or get_user_proactive_record(user_id).busy_count
            or await get_disturbance_tier(user_id, db=db) != "autonomous"
        ):
            return {"expressed": False, "action_id": None, "reason": "state_changed"}
        action = await desktop_action(db, user_id, choice.action_id, lock=True)
        asset = desktop_accepted_asset(action)
        if (
            not action.enabled
            or asset is None
            or trigger not in desktop_action_response(action).allowed_triggers
            or await desktop_autonomous_block_reason(
                db,
                current_state,
                action,
                asset,
                presentation_revision=presentation.revision,
            )
            is not None
        ):
            return {"expressed": False, "action_id": None, "reason": "no_longer_eligible"}
        try:
            command = await play_desktop_action(
                db,
                user_id,
                DesktopVideoPlayRequest(
                    action_id=choice.action_id,
                    expected_set_id=state.current.id,
                    reason=choice.reason,
                ),
                source="autonomous",
            )
        except DesktopVideoStateError:
            return {"expressed": False, "action_id": None, "reason": "state_changed"}
        if (
            presentation != get_presentation_snapshot(user_id)
            or contact_revision != get_user_proactive_record(user_id).contact_revision
            or not get_user_proactive_record(user_id).available
            or get_user_proactive_record(user_id).busy_count
            or await get_disturbance_tier(user_id, db=db) != "autonomous"
        ):
            return {"expressed": False, "action_id": None, "reason": "presentation_changed"}
        await db.commit()
    return {"expressed": True, "action_id": command.action_id, "reason": choice.reason}


async def drain_desktop_reviews() -> None:
    tasks = list(_REVIEWS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def resume_desktop_reviews(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        query = select(DesktopVideoProposal).where(DesktopVideoProposal.status == "pending")
        if user_id is not None:
            query = query.where(DesktopVideoProposal.user_id == user_id)
        rows = list(await db.scalars(query))
    for row in rows:
        async with SESSION_LOCAL() as db:
            await desktop_state(db, row.user_id, lock=True)
            current = await db.get(DesktopVideoProposal, row.id)
            if current is None or current.status != "pending":
                continue
            current.status = "deferred"
            current.review_reason = "评审已中断，请手动重试"
            await touch_desktop_state(db, row.user_id)
            await db.commit()
