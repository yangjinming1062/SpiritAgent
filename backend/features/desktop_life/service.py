"""桌面生活的组合准备、动作评审与播放协调。"""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from infrastructure.ai.prompt_engineer import VisualReasoningError, chat
from infrastructure.ai.selection import ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import (
    read_valid_image_data_uri,
    resolve_asset_reference,
    save_action_source_asset_async,
    save_companion_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
)
from infrastructure.assets.references import asset_paths
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.media_geometry import can_crop_to_aspect
from infrastructure.text import parse_llm_json, resolve_language, resolve_prompt_text, utc_now
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.action_library.identity import source_visual_key
from features.action_library.ports import read_source
from features.action_library.service import register_source_targets, remove_container_targets
from features.assets.lifecycle import (
    cleanup_asset_directories,
    cleanup_asset_directories_after_task,
    collect_asset_directory_paths,
    enqueue_asset_cleanup,
)
from features.companion.disturbance import get_disturbance_tier
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.companion.prompt_runtime import load_companion_prompt_context
from features.configuration.settings.values import get_user_setting
from features.conversation.context_window import load_recent_context_window
from features.desktop_life.creation import begin_opening_repair
from features.desktop_life.generation import (
    cancel_and_wait_desktop_video_jobs,
    cancel_desktop_video_job,
    desktop_context_hash,
    desktop_image_chain_exhausted,
    desktop_progress_can_resume,
    desktop_progress_has_unknown_submission,
    load_desktop_visual_snapshot,
    notify_desktop_set_updated,
    pause_desktop_video_job,
    prepare_uploaded_desktop_image,
    resolve_desktop_video_plan,
    schedule_desktop_video_job,
)
from features.desktop_life.geometry import (
    desktop_aspect_matches,
    desktop_aspect_ratio,
    probe_desktop_image,
    probe_desktop_video,
)
from features.desktop_life.models import DesktopVideoAction, DesktopVideoPlayback, DesktopVideoSet
from features.desktop_life.policy import DEFERRED_PLAY_INTENT_TTL_SECONDS, desktop_autonomous_block_reason
from features.desktop_life.ports import (
    design_shared_desktop_action,
    desktop_set_created,
    shared_desktop_proposal,
)
from features.desktop_life.prompts import (
    DESKTOP_CONTEXT_GUIDANCE,
    DESKTOP_EXTERNAL_VIDEO_TEMPLATES,
    DESKTOP_IDLE_EXPRESSION,
    DESKTOP_PRESETS,
    DESKTOP_VIDEO_BASE,
    DESKTOP_VIDEO_LOOP,
    DESKTOP_VIDEO_ONCE,
    DESKTOP_VIDEO_QUIET,
    desktop_source_reference_prompt,
)
from features.desktop_life.queries import activated_desktop_environment, desktop_action_source
from features.desktop_life.repository import (
    DesktopVideoAspectRatioError,
    DesktopVideoError,
    DesktopVideoModeError,
    DesktopVideoNotFoundError,
    DesktopVideoStateError,
    claim_desktop_play,
    desktop_accepted_asset,
    desktop_action,
    desktop_action_description,
    desktop_action_response,
    desktop_action_response_with_sync,
    desktop_asset_description,
    desktop_failed_opening_review,
    desktop_image_origin,
    desktop_preferences,
    desktop_progress,
    desktop_removed_video_task,
    desktop_set_response,
    desktop_set_responses,
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
    DesktopVideoAsset,
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
    DesktopVideoRefinementInput,
    DesktopVideoRefinePreviewResponse,
    DesktopVideoRefineRequest,
    DesktopVideoRenameSetRequest,
    DesktopVideoSetResponse,
    DesktopVideoStateResponse,
    DesktopVisualSnapshot,
)
from features.desktop_life.video_refinement import plan_desktop_video_refinement
from features.media.chain import (
    MEDIA_IDENTITY_ACCEPT_SCORE,
    FrozenMediaProvider,
    MediaChainState,
    spawn_removed_action_task_cancellation,
)
from features.media.character_images import CharacterImageInput, ImageChainState
from features.media.contracts import RemovedActionVideoTask

logger = get_logger(__name__)
_LOCKS: dict[int, asyncio.Lock] = {}


def _lock(user_id: int) -> asyncio.Lock:
    return _LOCKS.setdefault(user_id, asyncio.Lock())


def _effective_continuity(action: DesktopVideoAction) -> Literal["base", "independent"]:
    progress = desktop_progress(action) if action.stage != "complete" else None
    accepted = desktop_accepted_asset(action)
    return progress.continuity if progress else accepted.continuity if accepted else action.continuity


def _customization_priority(
    previous: DesktopVideoProgress | DesktopVideoAsset | None,
    feedback: str,
    video_feedback: str | None,
) -> Literal["general", "video"]:
    if video_feedback is not None:
        return "video" if video_feedback else "general"
    if previous is not None and previous.feedback == feedback:
        return previous.customization_priority
    return "general"


def _require_desktop_mode(user_id: int) -> None:
    presentation = get_presentation_snapshot(user_id)
    if presentation is None or presentation.mode != "desktop":
        raise DesktopVideoModeError("桌面模式当前未启用")


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
        sets=await desktop_set_responses(db, rows, current_id=state.current_set_id if state else None),
        version=state.version if state else 0,
    )


async def get_desktop_action_response(db: AsyncSession, user_id: int, action_id: int) -> DesktopVideoActionResponse:
    return await desktop_action_response_with_sync(db, await desktop_action(db, user_id, action_id, lock=True))


async def get_desktop_external_prompt(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    *,
    expected_set_id: int | None = None,
    requirements: str = "",
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    aspect_policy: Literal["ask", "keep"] = "ask",
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
        resolution_revision=expected_resolution_revision,
    )
    accepted = desktop_accepted_asset(action)
    confirmed = progress.pose_path if progress and progress.image_confirmed and action.stage != "complete" else ""
    confirmed = (
        confirmed
        or (row.base_image_path if _effective_continuity(action) == "base" else "")
        or (accepted.image_path if accepted else "")
    )
    references: list[DesktopVideoReference] = []
    chosen = (
        [("已确认的起始画面", confirmed)]
        if confirmed
        else [
            (
                "全身身份参考图" if snapshot.identity_reference_role == "fullbody" else "头像参考图",
                snapshot.primary_reference_path,
            ),
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
    has_base = _effective_continuity(action) == "base" and bool(confirmed)
    output_width, output_height = row.target_width, row.target_height
    if confirmed:
        mode, width, height, _data = await _image_geometry(db, row, confirmed, aspect_policy, action=action)
        if mode == "keep":
            output_width, output_height = width, height
    language = resolve_language(await get_user_setting(db, user_id, "language"))
    extra = requirements.strip()
    description = desktop_action_description(action)
    plan = (progress.creation_plan if progress else None) or (accepted.creation_plan if accepted else None)
    if plan is not None:
        description = plan.activity + "\n" + plan.motion_requirements
    if language == "en":
        kind = "looping" if action.kind == "loop" else "single-action"
        cycle = (
            DESKTOP_VIDEO_QUIET[language]
            if action.key == "idle"
            else "Image 1 is the required opening and ending frame. Complete the target activity once at its required speed, pausing only where appropriate. After completion, return naturally through the actual action to the opening pose, gaze, support contacts, prop positions and visible prop states, settling across the boundary. Keep the camera and lighting stable; do not add unrelated back-and-forth motion, reverse actions or reset visible prop states to match the endpoints."
            if has_base and action.kind != "loop"
            else "Continue the target activity, pausing only where appropriate. The ending pose, gaze, support contacts, prop positions and visible prop states join the opening naturally, with continuous motion direction and speed across the boundary. Preserve repetition required by the target itself; do not add unrelated back-and-forth motion, reverse actions or reset visible prop states to close the loop."
            if action.kind == "loop"
            else "Complete the target action once at its required speed, pausing only where appropriate, then hold its natural finishing state."
        )
    else:
        kind = "循环" if action.kind == "loop" else "单次"
        cycle = (
            DESKTOP_VIDEO_QUIET[language]
            if action.key == "idle"
            else "图 1 是必须使用的起止画面。" + DESKTOP_VIDEO_BASE
            if has_base and action.kind != "loop"
            else DESKTOP_VIDEO_LOOP
            if action.kind == "loop"
            else DESKTOP_VIDEO_ONCE
        )
    prompt = DESKTOP_EXTERNAL_VIDEO_TEMPLATES[language].format(
        duration_seconds=action.duration_seconds,
        references=(
            "图 1 是已确认的起始画面，人物外观、穿着、环境、物品、构图和光线均以此为准。"
            if confirmed and language != "en"
            else "Image 1 is the confirmed opening frame. Preserve its visible identity, clothing, environment, props, framing and lighting."
            if confirmed
            else desktop_source_reference_prompt(
                snapshot.identity_reference_role,
                language=language,
                include_scene=True,
            )
        ),
        kind=kind,
        motion=description,
        cycle=cycle,
    )
    output_ratio = desktop_aspect_ratio(output_width, output_height)
    prompt += (
        f"\nDesktop target: {row.target_width}×{row.target_height}. Output aspect ratio: {output_ratio}; use the highest available native resolution and preserve the confirmed image framing."
        if language == "en" and confirmed
        else f"\nDesktop target: {row.target_width}×{row.target_height}. Output aspect ratio: {output_ratio}; use the highest available native resolution and compose a coherent new opening frame for the activity according to the reference roles above."
        if language == "en"
        else f"\n桌景目标分辨率为 {row.target_width}×{row.target_height}，本次输出画幅为 {output_ratio}；使用可用的最高原生清晰度，保留已确认图片的构图。"
        if confirmed
        else f"\n桌景目标分辨率为 {row.target_width}×{row.target_height}，本次输出画幅为 {output_ratio}；使用可用的最高原生清晰度，按上述参考职责安排符合活动的连贯新开场。"
    )
    conditions = {"use_when": json.loads(action.use_when_json), "avoid_when": json.loads(action.avoid_when_json)}
    prompt += (
        "\nUsage conditions: "
        + json.dumps(conditions, ensure_ascii=False)
        + ". Use the actual body, available props and space; if required conditions are missing, explain why instead of redesigning the body or room."
        if language == "en" and confirmed
        else "\nUsage conditions: "
        + json.dumps(conditions, ensure_ascii=False)
        + ". Preserve the reference body structure and current styling. Compose a new opening using image 3's environment, with compatible everyday props needed by the activity; if the conditions cannot support it, explain why. Do not redesign the body or replace the environment without support from the references."
        if language == "en"
        else "\n适用与避免条件："
        + json.dumps(conditions, ensure_ascii=False)
        + "。使用实际身体、已有物品和空间；必要条件缺失时说明原因，不能改造身体或房间布局来完成活动。"
        if confirmed
        else "\n适用与避免条件："
        + json.dumps(conditions, ensure_ascii=False)
        + "。保留参考中的身体结构和当前造型，按图 3 的环境安排新开场与活动所需的相容日用品；必要条件无法支持时说明原因，不能改造身体或无依据替换环境。"
    )
    customization = progress or accepted
    if customization is not None and (customization.feedback or customization.video_feedback):
        saved_requirements = json.dumps(
            {
                "feedback": customization.feedback,
                "video_feedback": customization.video_feedback,
                "customization_priority": customization.customization_priority,
            },
            ensure_ascii=False,
        )
        prompt += (
            "\nSaved user requirements (creative material): "
            + saved_requirements
            + ". For explicitly conflicting dimensions, customization_priority selects the newer requirement; retain the rest. The confirmed opening remains the visual authority, and motion requirements apply only to the video."
            if language == "en"
            else "\n继续生效的用户定制资料："
            + saved_requirements
            + "。仅在明确冲突的维度按 customization_priority 指定的较新要求处理，其余继续生效；已确认起始图仍是视觉依据，运动要求只作用于视频过程。"
        )
    if extra:
        if confirmed:
            prompt += (
                "\nAdditional user requirements adjust only the activity and expressions compatible with image 1; "
                f"preserve its opening composition and the playback requirements above: {extra}"
                if language == "en"
                else f"\n用户本次调整仅改变与图 1 相容的活动和过程神态，保留图中的开场构图及上述播放要求：{extra}"
            )
        else:
            prompt += (
                "\nAdditional user requirements (take precedence for the specified activity, expression and opening framing; "
                f"preserve the identity, styling, environment and playback mode above): {extra}"
                if language == "en"
                else f"\n用户本次调整（对应的活动、神态与开场构图细节以此为准，身份、造型、环境与播放方式沿用上述要求）：{extra}"
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
    return await shared_desktop_proposal(db, user_id, proposal_id)


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
    target_width: int,
    target_height: int,
    is_default: bool = False,
    initial_feedback: str | None = None,
) -> DesktopVideoSet:
    row = DesktopVideoSet(
        user_id=user_id,
        avatar_id=snapshot.identity.avatar_id,
        outfit_id=snapshot.outfit_id,
        scene_id=snapshot.scene_id,
        source_visual_key=source_visual_key(
            snapshot.identity_hash,
            snapshot.outfit_hash,
            portrait_hash=snapshot.portrait_hash,
            identity_reference_role=snapshot.identity_reference_role,
        ),
        context_hash=desktop_context_hash(snapshot),
        context_json=snapshot.model_dump_json(),
        title=name,
        target_width=target_width,
        target_height=target_height,
        resolution_revision=1,
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
                generation_state_json=DesktopVideoProgress(
                    generation_id=uuid4().hex,
                    target_width=target_width,
                    target_height=target_height,
                    resolution_revision=1,
                    aspect_ratio=desktop_aspect_ratio(target_width, target_height),
                    source="user_requested",
                    feedback=initial_feedback,
                    image_only=True,
                    duration_seconds=preset.duration_seconds,
                    continuity=preset.continuity,
                    base_version=1 if preset.continuity == "base" else None,
                    replace_base=preset.continuity == "base",
                ).model_dump_json()
                if preset.key == "idle" and source == "ai" and initial_feedback is not None
                else None,
                needs_update=False,
                update_pending=False,
            ),
        )
    await db.flush()
    await touch_desktop_state(db, user_id)
    action_source = await read_source(db, user_id, "desktop", row.id)
    if action_source is None:
        raise RuntimeError("新桌景的共享动作来源无法读取")
    await register_source_targets(db, action_source)
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
                target_width=request.target_width,
                target_height=request.target_height,
                initial_feedback=request.feedback.strip(),
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
            if (original.scene_id, original.outfit_id, row.source, row.target_width, row.target_height) != (
                request.scene_id,
                request.outfit_id,
                request.source,
                request.target_width,
                request.target_height,
            ):
                raise DesktopVideoStateError("这个创建请求已经用于另一套画面")
        set_id = row.id
    await desktop_set_created(user_id, set_id)
    if idle_id is not None:
        try:
            await generate_desktop_image(user_id, idle_id, feedback=request.feedback.strip())
        except DesktopVideoError as exc:
            await _record_preparation_error(user_id, idle_id, str(exc))
    async with SESSION_LOCAL() as db:
        row = await _suite(db, user_id, set_id)
        state = await desktop_state(db, user_id)
        return await desktop_set_response(db, row, current_id=state.current_set_id if state else None)


async def rename_desktop_video_set(
    db: AsyncSession,
    user_id: int,
    set_id: int,
    request: DesktopVideoRenameSetRequest,
) -> DesktopVideoSetResponse:
    row = await _suite(db, user_id, set_id)
    row.title = request.name
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
    directory = f"desktop/{set_id}"
    paths |= await collect_asset_directory_paths(user_id, directory)
    removed_tasks = []
    await remove_container_targets(db, user_id, "desktop", set_id)
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
    await enqueue_asset_cleanup(db, user_id, paths, expedite=True)
    await touch_desktop_state(db, user_id)
    await db.commit()
    await cancel_and_wait_desktop_video_jobs([action.id for action in actions])
    spawn_removed_action_task_cancellation(user_id, removed_tasks)
    await cleanup_asset_directories(user_id, [directory])
    return await get_desktop_video_state(db, user_id)


async def _record_preparation_error(user_id: int, action_id: int, error: str) -> None:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        if action.status != "queued":
            return
        action.status, action.stage, action.error = "failed", "prepare", error
        await touch_desktop_state(db, user_id)
        await db.commit()


async def ensure_current_desktop_videos(
    user_id: int,
    *,
    source: str = "user_requested",
    allow_generation: bool = True,
    target_width: int | None = None,
    target_height: int | None = None,
) -> DesktopVideoStateResponse:
    idle_id = None
    created_set_id = None
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await desktop_state(db, user_id, create=True)
        if target_width is not None and target_height is not None and not state.default_created:
            state.target_width, state.target_height = target_width, target_height
            await db.commit()
            state = await desktop_state(db, user_id, create=True)
        if not state.default_created:
            if not state.target_width or not state.target_height:
                return await get_desktop_video_state(db, user_id)
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
                target_width=state.target_width,
                target_height=state.target_height,
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
            created_set_id = row.id
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
    if created_set_id is not None:
        await desktop_set_created(user_id, created_set_id)
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
    resolution_revision: int | None = None,
) -> None:
    if draft_id == "none" and progress is not None:
        raise DesktopVideoStateError("已有新的制作草稿，请刷新后重试")
    if draft_id is not None and draft_id != "none" and (progress is None or progress.generation_id != draft_id):
        raise DesktopVideoStateError("制作草稿已变化，请刷新后重试")
    if base_version is not None and base_version != row.base_version:
        raise DesktopVideoStateError("基础画面已变化，请刷新后重试")
    if resolution_revision is not None and resolution_revision != row.resolution_revision:
        raise DesktopVideoStateError("桌景分辨率已变化，请刷新后重试")
    if progress is not None and progress.resolution_revision != row.resolution_revision:
        raise DesktopVideoStateError("制作草稿使用的桌景分辨率已变化，请重新制作")


def _geometry_mode(
    row: DesktopVideoSet,
    width: int,
    height: int,
    aspect_policy: str,
    media_kind: str = "image",
) -> Literal["target", "keep"]:
    if aspect_policy not in {"ask", "keep", "expand"}:
        raise DesktopVideoStateError("不支持的画幅处理方式")
    if aspect_policy == "keep":
        return "keep"
    if desktop_aspect_matches(width, height, row.target_width, row.target_height):
        return "target"
    if aspect_policy == "ask":
        raise DesktopVideoAspectRatioError(width, height, row.target_width, row.target_height, media_kind)
    return "target"


async def _image_geometry(
    db: AsyncSession,
    row: DesktopVideoSet,
    image: str,
    aspect_policy: str,
    *,
    action: DesktopVideoAction | None = None,
) -> tuple[Literal["target", "keep"], int, int, bytes]:
    resolved = resolve_asset_reference(image)
    if resolved is None:
        raise DesktopVideoStateError("图片无法读取，请重新上传")
    data = await asyncio.to_thread(resolved[0].read_bytes)
    width, height = await asyncio.to_thread(probe_desktop_image, data)
    if aspect_policy != "ask":
        return _geometry_mode(row, width, height, aspect_policy), width, height, data
    # 已明确保留原画幅的基准及派生动作继续使用同一例外。
    actions = await db.scalars(select(DesktopVideoAction).where(DesktopVideoAction.set_id == row.id))
    for other in actions:
        accepted = desktop_accepted_asset(other)
        if (
            accepted is not None
            and accepted.image_path == image
            and accepted.resolution_revision == row.resolution_revision
            and accepted.geometry_mode == "keep"
        ):
            return "keep", width, height, data
        prior = desktop_progress(other)
        if (
            prior is not None
            and prior.pose_path == image
            and prior.resolution_revision == row.resolution_revision
            and prior.geometry_mode == "keep"
        ):
            return "keep", width, height, data
    try:
        geometry_mode = _geometry_mode(row, width, height, aspect_policy)
    except DesktopVideoAspectRatioError as exc:
        if (
            action is not None
            and _effective_continuity(action) == "base"
            and action.key != "idle"
            and image == row.base_image_path
        ):
            idle = await _shared_base_adjustment_action(db, row)
            exc.details.update(adjust_action_id=idle.id, adjust_action_name=idle.name)
        raise
    return geometry_mode, width, height, data


async def _shared_base_adjustment_action(db: AsyncSession, row: DesktopVideoSet) -> DesktopVideoAction:
    idle = await db.scalar(
        select(DesktopVideoAction).where(
            DesktopVideoAction.user_id == row.user_id,
            DesktopVideoAction.set_id == row.id,
            DesktopVideoAction.key == "idle",
        ),
    )
    if idle is None:
        raise DesktopVideoStateError("找不到安静相伴，请重新创建这套桌景")
    return idle


async def generate_desktop_image(
    user_id: int,
    action_id: int,
    *,
    feedback: str | None = None,
    video_feedback: str | None = None,
    regenerate: bool = False,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    aspect_policy: str = "ask",
    source: str = "user_requested",
    auto_continue: bool = False,
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        state = await desktop_state(db, user_id, create=True)
        if source == "autonomous" and (not state.autonomous_enabled or not SETTINGS.action_autocreate_enabled):
            raise DesktopVideoStateError("自主制作动作当前已关闭")
        if action.definition_id is not None:
            source_snapshot = await desktop_action_source(db, user_id, row.id)
            if source_snapshot is None or not source_snapshot.ready:
                raise DesktopVideoStateError(
                    source_snapshot.blocked_reason if source_snapshot else "桌景冻结参考资料无法读取",
                )
        old = desktop_progress(action)
        accepted = desktop_accepted_asset(action)
        if feedback is None:
            feedback = (
                old.feedback
                if old is not None and action.stage != "complete"
                else accepted.feedback
                if accepted
                else ""
            )
        _validate_draft(
            row,
            old if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        continuity = _effective_continuity(action)
        if action.key != "idle" and continuity == "base":
            continuity = "independent"
        if action.status == "processing" and action.stage != "paused":
            return await desktop_action_response_with_sync(db, action)
        if old is not None and desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先放弃本次制作再重新开始")
        exhausted_expansion = old is not None and old.expand_image and desktop_image_chain_exhausted(old)
        if exhausted_expansion and source != "user_requested":
            raise DesktopVideoStateError("本次图片制作没有可用结果，请重新制作图片")
        resume = (
            old is not None
            and old.image_only
            and action.stage in {"paused", "image", "prepare", "planning", "checking", "repairing"}
            and feedback == old.feedback
            and (video_feedback is None or video_feedback == old.video_feedback)
            and not regenerate
            and not exhausted_expansion
            and (old.paused_by_user or desktop_progress_can_resume(old))
        )
        removed_task = None
        if resume:
            progress = old
            progress.paused_by_user = False
            progress.source = source
            progress.auto_continue = auto_continue
            progress.failure_reason = ""
        else:
            retry_expansion = old is not None and old.expand_image and not regenerate
            previous = (old.pose_path or old.previous_pose_path) if old else None
            if old is not None and retry_expansion and not previous:
                previous = old.previous_pose_path or old.source_image_path
            accepted = desktop_accepted_asset(action)
            previous = previous or (accepted.image_path if accepted else None)
            if previous is None and auto_continue and action.definition_id is not None:
                previous = row.base_image_path
            if not previous and action.key != "idle" and action.continuity == "base":
                previous = row.base_image_path
            geometry_mode: Literal["target", "keep"] = "target"
            frame_width, frame_height = row.target_width, row.target_height
            expand_image = False
            if previous and not regenerate:
                image_aspect_policy = "expand" if retry_expansion else aspect_policy
                geometry_mode, frame_width, frame_height, _data = await _image_geometry(
                    db,
                    row,
                    previous,
                    image_aspect_policy,
                )
                expand_image = image_aspect_policy == "expand" and not desktop_aspect_matches(
                    frame_width,
                    frame_height,
                    row.target_width,
                    row.target_height,
                )
            image_origin = await desktop_image_origin(db, user_id, row.id, previous)
            video_origin = (
                old.candidate.video_origin
                if old and old.candidate and action.stage != "complete"
                else accepted.video_origin
                if accepted
                else None
            )
            selection_mode: Literal["next", "current"] = "next" if regenerate else "current"
            removed_task = desktop_removed_video_task(action)
            old_paths = asset_paths(action.generation_state_json, user_id)
            action.attempt += 1
            progress = DesktopVideoProgress(
                target_width=row.target_width,
                target_height=row.target_height,
                resolution_revision=row.resolution_revision,
                aspect_ratio=desktop_aspect_ratio(frame_width, frame_height)
                if geometry_mode == "keep"
                else desktop_aspect_ratio(row.target_width, row.target_height),
                geometry_mode=geometry_mode,
                frame_width=frame_width,
                frame_height=frame_height,
                expand_image=expand_image,
                source_image_path=(old.source_image_path if old else None)
                or (accepted.source_image_path if accepted else None)
                or (previous if expand_image else None),
                generation_id=uuid4().hex,
                source=source,
                feedback=feedback,
                video_feedback=video_feedback
                if video_feedback is not None
                else old.video_feedback
                if old
                else accepted.video_feedback
                if accepted
                else "",
                customization_priority=_customization_priority(old or accepted, feedback, video_feedback),
                action_description=desktop_action_description(action),
                creation_plan=old.creation_plan
                if old and old.feedback == feedback
                else accepted.creation_plan
                if accepted and accepted.feedback == feedback
                else None,
                duration_seconds=action.target_duration_seconds,
                continuity=continuity,
                base_version=row.base_version + 1 if continuity == "base" else None,
                replace_base=action.key == "idle",
                image_only=True,
                auto_continue=auto_continue,
                previous_pose_path=previous,
                previous_image_origin=image_origin,
                image_selection=ProviderSelection(origin=image_origin, mode=selection_mode),
                video_selection=ProviderSelection(origin=video_origin, mode=selection_mode),
                regenerate_image=regenerate,
            )
            await enqueue_asset_cleanup(db, user_id, old_paths - ({previous} if previous else set()))
        local_fit = (
            progress.expand_image
            and not progress.feedback.strip()
            and can_crop_to_aspect(
                progress.frame_width or progress.target_width,
                progress.frame_height or progress.target_height,
                progress.target_width,
                progress.target_height,
            )
        )
        if not local_fit:
            require_new_generation_call(user_id)
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error = "processing", "prepare", None
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
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
    expected_action_version: int,
    expected_set_id: int | None = None,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    aspect_policy: str = "ask",
) -> DesktopVideoActionResponse:
    try:
        width, height = await asyncio.to_thread(probe_desktop_image, data)
    except (ValueError, OSError) as exc:
        raise DesktopVideoStateError("图片无法读取，请上传完整的图片") from exc
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        if action.version != expected_action_version:
            raise DesktopVideoStateError("当前成品已变化，请刷新后重试")
        row = await _suite(db, user_id, action.set_id)
        if expected_set_id is not None and expected_set_id != row.id:
            raise DesktopVideoStateError("桌景套装已变化")
        _validate_draft(
            row,
            desktop_progress(action) if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        continuity: Literal["base", "independent"] = "base" if action.key == "idle" else "independent"
        if action.status == "processing" and action.stage != "paused":
            raise DesktopVideoStateError("请先取消当前制作")
        old = desktop_progress(action)
        if old is not None and desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        geometry_mode = _geometry_mode(row, width, height, aspect_policy)
        old_json, old_status, old_stage = action.generation_state_json, action.status, action.stage
        accepted = desktop_accepted_asset(action)
        action.attempt += 1
        progress = DesktopVideoProgress(
            target_width=row.target_width,
            target_height=row.target_height,
            resolution_revision=row.resolution_revision,
            aspect_ratio=desktop_aspect_ratio(row.target_width, row.target_height),
            generation_id=uuid4().hex,
            source="user_requested",
            feedback=old.feedback if old else accepted.feedback if accepted else "",
            video_feedback=old.video_feedback if old else accepted.video_feedback if accepted else "",
            customization_priority=old.customization_priority
            if old
            else accepted.customization_priority
            if accepted
            else "general",
            geometry_mode=geometry_mode,
            expand_image=aspect_policy == "expand"
            and geometry_mode == "target"
            and not desktop_aspect_matches(width, height, row.target_width, row.target_height),
            frame_width=width,
            frame_height=height,
            duration_seconds=action.target_duration_seconds,
            continuity=continuity,
            base_version=row.base_version + 1 if continuity == "base" else None,
            replace_base=action.key == "idle",
            image_only=True,
            action_description=desktop_action_description(action),
        )
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
        if not progress.expand_image:
            progress.pose_path = saved_path
        async with _lock(user_id), SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id, lock=True)
            row = await _suite(db, user_id, action.set_id)
            current = desktop_progress(action)
            _validate_draft(row, current, draft_id=progress.generation_id, base_version=baseline)
            action.generation_state_json = progress.model_dump_json()
            action.status, action.stage, action.error = (
                ("processing", "prepare", None) if progress.expand_image else ("review_pending", "image_review", None)
            )
            await enqueue_asset_cleanup(db, user_id, asset_paths(old_json, user_id))
            await touch_desktop_state(db, user_id)
            await db.commit()
    except (Exception, asyncio.CancelledError) as exc:
        recovered = False
        async with SESSION_LOCAL() as db:
            await desktop_state(db, user_id, lock=True)
            await enqueue_asset_cleanup(
                db,
                user_id,
                {saved_path, progress.source_image_path} - {None},
            )
            saved_action = await db.scalar(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.user_id == user_id,
                    DesktopVideoAction.id == action_id,
                ),
            )
            current = desktop_progress(saved_action) if saved_action else None
            if saved_action is not None and current is not None and current.generation_id == progress.generation_id:
                if (
                    saved_path
                    and progress.source_image_path
                    and current.source_image_path == progress.source_image_path
                    and (current.pose_path == saved_path or current.previous_pose_path == saved_path)
                ):
                    action, progress, recovered = saved_action, current, True
                else:
                    saved_action.generation_state_json, saved_action.status, saved_action.stage = (
                        old_json,
                        old_status,
                        old_stage,
                    )
                    saved_action.error = "图片上传未完成，请重试"
                    await touch_desktop_state(db, user_id)
            await db.commit()
        if saved_action is None:
            cleanup_asset_directories_after_task(user_id, [directory])
        if isinstance(exc, asyncio.CancelledError):
            raise
        if not recovered:
            raise
    cancel_desktop_video_job(action_id)
    spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    if progress.expand_image and action.status == "processing" and not progress.paused_by_user:
        schedule_desktop_video_job(user_id, action_id)
    async with SESSION_LOCAL() as db:
        return await get_desktop_action_response(db, user_id, action_id)


async def confirm_desktop_image(
    user_id: int,
    action_id: int,
    *,
    draft_id: str,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    generate_video: bool = True,
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        progress = desktop_progress(action)
        _validate_draft(
            row,
            progress,
            draft_id=draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        if progress is None or not progress.pose_path:
            raise DesktopVideoStateError("请先准备基础图片")
        if desktop_failed_opening_review(action, progress):
            raise DesktopVideoStateError("当前画面尚未完成活动检查，请先重试制作")
        if desktop_progress_has_unknown_submission(progress):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先放弃本次制作再重新开始")
        if progress.image_confirmed and action.stage not in {"image_review", "image_confirmed"}:
            return await desktop_action_response_with_sync(db, action)
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
            require_new_generation_call(user_id)
        action.generation_state_json = progress.model_dump_json()
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
    if generate_video:
        schedule_desktop_video_job(user_id, action_id)
    return response


async def pause_desktop_action(
    user_id: int,
    action_id: int,
    *,
    draft_id: str | None = None,
) -> DesktopVideoActionResponse:
    async with _lock(user_id):
        async with SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id, lock=True)
            row = await _suite(db, user_id, action.set_id)
            progress = desktop_progress(action)
            _validate_draft(row, progress, draft_id=draft_id)
            if progress is None or action.status != "processing" or action.stage == "paused":
                return await desktop_action_response_with_sync(db, action)
            if action.stage == "image_upload":
                raise DesktopVideoStateError("图片正在保存，请等待完成")
            generation_id = progress.generation_id
        # Let the cancelled workflow save its last known handle before taking a new row lock.
        await pause_desktop_video_job(action_id)
        async with SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id, lock=True)
            progress = desktop_progress(action)
            if progress is not None and progress.generation_id == generation_id and action.status == "processing":
                progress.paused_by_user = True
                progress.submission_unknown = desktop_progress_has_unknown_submission(progress)
                action.generation_state_json = progress.model_dump_json()
                action.stage, action.error = "paused", None
                await touch_desktop_state(db, user_id)
                await db.commit()
            return await desktop_action_response_with_sync(db, action)


async def retry_desktop_action(
    user_id: int,
    action_id: int,
    *,
    draft_id: str,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        progress = desktop_progress(action)
        _validate_draft(
            row,
            progress,
            draft_id=draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        if progress is None:
            raise DesktopVideoStateError("制作草稿已失效")
        running = action.status == "processing" and action.stage != "paused"
        if running or desktop_progress_has_unknown_submission(progress):
            return await desktop_action_response_with_sync(db, action)
        if (
            action.stage in {"complete", "review", "image_review", "image_confirmed"}
            and not desktop_failed_opening_review(action, progress)
            or progress.failure_reason == "activity_unsupported"
        ):
            return await desktop_action_response_with_sync(db, action)
        if (
            progress.source == "external_upload"
            and progress.video_refinement is None
            and (
                progress.failure_reason in {"material_rejected", "queue_stopped"}
                or not (progress.source_path or progress.candidate)
            )
        ):
            return await desktop_action_response_with_sync(db, action)
        restart_refinement = progress.video_refinement is not None and not desktop_progress_can_resume(progress)
        image = ImageChainState.model_validate_json(progress.image_chain_json) if progress.image_chain_json else None
        new_image = action.status == "failed" and (
            progress.failure_reason in {"image_unsuitable", "review_invalid"}
            or bool(
                progress.image_only
                and image is not None
                and (image.stop_reason in {"exhausted", "goal_review_invalid"} or image.phase == "complete"),
            )
        )
        if new_image and not progress.video_refinement and progress.source != "external_upload":
            reference = progress.pose_path or progress.previous_pose_path
            if image is not None and image.candidates:
                safe = max(
                    (
                        candidate
                        for candidate in image.candidates
                        if candidate.score is not None and candidate.score >= MEDIA_IDENTITY_ACCEPT_SCORE
                    ),
                    key=lambda candidate: candidate.score,
                    default=None,
                )
                reference = (
                    safe.path
                    if safe
                    else (
                        image.inputs.identity_reference
                        if isinstance(image.inputs, CharacterImageInput) and image.inputs.image_edit
                        else None
                    )
                )
            begin_opening_repair(
                progress,
                reason=progress.repair_reason or action.error or "重新准备满足活动要求的起始画面",
                reference_path=reference,
                separate_base=progress.continuity == "base" and action.key != "idle",
                new_budget=True,
                replace_base=action.key == "idle",
            )
            require_new_generation_call(user_id)
            action.generation_state_json = progress.model_dump_json()
            action.status, action.stage, action.error = "processing", "repairing", None
            await touch_desktop_state(db, user_id)
            await db.commit()
            response = await desktop_action_response_with_sync(db, action)
        else:
            response = None
        resumable = action.stage == "paused" or desktop_progress_can_resume(progress)
        feedback, image_only, auto_continue = progress.feedback, progress.image_only, progress.auto_continue
    if restart_refinement:
        return await _restart_desktop_refinement(
            user_id,
            action_id,
            draft_id=draft_id,
            expected_base_version=expected_base_version,
            expected_resolution_revision=expected_resolution_revision,
        )
    if response is not None:
        schedule_desktop_video_job(user_id, action_id)
        return response
    if resumable:
        return await resume_desktop_action(
            user_id,
            action_id,
            draft_id=draft_id,
            expected_base_version=expected_base_version,
            expected_resolution_revision=expected_resolution_revision,
        )
    if image_only:
        return await generate_desktop_image(
            user_id,
            action_id,
            feedback=feedback,
            expected_draft_id=draft_id,
            expected_base_version=expected_base_version,
            expected_resolution_revision=expected_resolution_revision,
            auto_continue=auto_continue,
        )
    return await generate_desktop_action(
        user_id,
        action_id,
        feedback=feedback,
        expected_draft_id=draft_id,
        expected_base_version=expected_base_version,
        expected_resolution_revision=expected_resolution_revision,
    )


async def _restart_desktop_refinement(
    user_id: int,
    action_id: int,
    *,
    draft_id: str,
    expected_base_version: int | None,
    expected_resolution_revision: int | None,
) -> DesktopVideoActionResponse:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id)
        row = await _suite(db, user_id, action.set_id)
        progress = desktop_progress(action)
        _validate_draft(
            row,
            progress,
            draft_id=draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        if progress is None or progress.video_refinement is None:
            raise DesktopVideoStateError("视频调整输入已失效")
        refinement = progress.video_refinement
        source = resolve_asset_reference(refinement.video_path)
        if source is None:
            raise DesktopVideoStateError("原视频无法读取，请重新选择视频")
        observed_base, observed_resolution = row.base_version, row.resolution_revision
    seconds, providers = await plan_desktop_video_refinement(
        user_id,
        await asyncio.to_thread(source[0].read_bytes),
        source[0].suffix,
        aspect_ratio=progress.aspect_ratio,
        target_width=progress.target_width,
        target_height=progress.target_height,
        geometry_mode=progress.geometry_mode,
        selection=ProviderSelection(origin=refinement.video_origin, mode="current"),
        feedback=progress.video_adjustment,
    )
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        old = desktop_progress(action)
        _validate_draft(
            row,
            old,
            draft_id=draft_id,
            base_version=observed_base,
            resolution_revision=observed_resolution,
        )
        if action.status == "processing" and action.stage != "paused":
            return await desktop_action_response_with_sync(db, action)
        if old is None or old.video_refinement is None or desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("视频调整状态已变化，请刷新后重试")
        require_new_generation_call(user_id)
        removed_task = desktop_removed_video_task(action)
        old_paths = asset_paths(action.generation_state_json, user_id)
        progress = old.model_copy(
            deep=True,
            update={
                "generation_id": uuid4().hex,
                "source": "user_requested",
                "paused_by_user": False,
                "duration_seconds": seconds,
                "video_chain_json": MediaChainState(providers=providers).model_dump_json(),
                "provider_task_id": None,
                "download_url": None,
                "source_path": None,
                "candidate": None,
                "identity_score": None,
                "review_verdict": None,
                "review_reason": "",
                "failure_reason": "",
                "submission_unknown": False,
                "force_review": True,
                "reprocess_only": False,
            },
        )
        action.attempt += 1
        action.duration_seconds = seconds
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error = "processing", "video", None
        await enqueue_asset_cleanup(db, user_id, old_paths - asset_paths(action.generation_state_json, user_id))
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
    cancel_desktop_video_job(action_id)
    spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    schedule_desktop_video_job(user_id, action_id)
    return response


async def resume_desktop_action(
    user_id: int,
    action_id: int,
    *,
    draft_id: str,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        progress = desktop_progress(action)
        _validate_draft(
            row,
            progress,
            draft_id=draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        if progress is None:
            raise DesktopVideoStateError("制作草稿已失效")
        if action.status == "processing" and action.stage != "paused":
            return await desktop_action_response_with_sync(db, action)
        if desktop_progress_has_unknown_submission(progress):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        if action.stage in {
            "complete",
            "review",
            "image_review",
            "image_confirmed",
        } and not desktop_failed_opening_review(action, progress):
            raise DesktopVideoStateError("请先确认当前制作结果")
        if progress.failure_reason == "material_rejected":
            raise DesktopVideoStateError(
                "上传的视频无法使用，请更换视频后重新上传"
                if progress.source == "external_upload"
                else "生成的视频无法使用，请重新制作视频",
            )
        if progress.failure_reason == "queue_stopped":
            raise DesktopVideoStateError("本次视频制作已结束，请重新制作视频")
        if progress.expand_image and desktop_image_chain_exhausted(progress):
            raise DesktopVideoStateError("本次图片制作没有可用结果，请重新制作图片")
        if progress.source == "external_upload":
            if not progress.source_path and progress.candidate is None:
                raise DesktopVideoStateError("上传的视频尚未保存完成，请稍后重试或重新上传")
        elif not progress.reprocess_only:
            require_new_generation_call(user_id)
        progress.paused_by_user = False
        progress.failure_reason = ""
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error = (
            "processing",
            "process" if progress.reprocess_only else "prepare",
            None,
        )
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
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
        action.status = (
            "ready"
            if desktop_accepted_asset(action)
            else "not_generated"
            if action.definition_id is not None
            else "queued"
        )
        action.stage, action.error, action.update_pending = (
            "complete" if action.accepted_asset_json else "prepare",
            None,
            False,
        )
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
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
        ids = [
            action.id
            for action in actions
            if action.stage == "complete"
            or (desktop_progress(action) is None or desktop_progress(action).continuity == "base")
        ]
        invalidated = list(
            await db.scalars(
                select(DesktopVideoAction.id).where(
                    DesktopVideoAction.user_id == user_id,
                    DesktopVideoAction.set_id == set_id,
                    DesktopVideoAction.key != "idle",
                    DesktopVideoAction.needs_update.is_(True),
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
    feedback: str | None = None,
    video_feedback: str | None = None,
    source: str = "user_requested",
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    aspect_policy: str = "ask",
    pending_update: bool = False,
    auto_continue: bool | None = None,
) -> DesktopVideoActionResponse:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        if pending_update and (not action.enabled or not action.update_pending):
            return await desktop_action_response_with_sync(db, action)
        progress = desktop_progress(action)
        _validate_draft(
            row,
            progress if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        if action.status == "processing" and action.stage != "paused":
            return await desktop_action_response_with_sync(db, action)
        if progress is not None and desktop_progress_has_unknown_submission(progress):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先等待结果或放弃本次制作")
        if progress is not None and action.stage == "image_review":
            raise DesktopVideoStateError("请先确认图片后再生成视频")
        accepted = desktop_accepted_asset(action)
        continue_automatically = (
            auto_continue if auto_continue is not None else action.definition_id is not None and accepted is None
        )
        if feedback is None:
            feedback = (
                progress.feedback
                if progress is not None and action.stage != "complete"
                else accepted.feedback
                if accepted
                else ""
            )
        selected_video_feedback = (
            video_feedback
            if video_feedback is not None
            else progress.video_feedback
            if progress is not None and action.stage != "complete"
            else accepted.video_feedback
            if accepted
            else ""
        )
        continuity = _effective_continuity(action)
        image = row.base_image_path if continuity == "base" else accepted.image_path if accepted else None
        if progress is not None and progress.image_confirmed and progress.pose_path and action.stage != "complete":
            image = progress.pose_path
        needs_image = not image or (progress is not None and progress.image_only and action.stage != "complete")
        if continuity == "base" and action.key != "idle" and not row.base_image_path:
            raise DesktopVideoStateError("请先制作安静相伴")
        kind, duration = action.kind, action.target_duration_seconds
        resumable = (
            progress is not None
            and progress.image_confirmed
            and action.stage not in {"complete", "review"}
            and feedback == progress.feedback
            and selected_video_feedback == progress.video_feedback
            and desktop_progress_can_resume(progress)
        )
        frozen_chain = progress.video_chain_json if resumable else None
        video_selection = (
            progress.video_selection
            if progress is not None and not progress.video_chain_json and progress.video_selection is not None
            else ProviderSelection(
                origin=progress.candidate.video_origin
                if progress is not None and progress.candidate is not None and action.stage != "complete"
                else accepted.video_origin
                if accepted
                else None,
                mode="next",
            )
        )
        observed_draft = progress.generation_id if progress is not None and action.stage != "complete" else "none"
        observed_base = row.base_version
        observed_resolution = row.resolution_revision
        target_width, target_height = row.target_width, row.target_height
        adjustment_action_id = action_id
        adjustment_draft = observed_draft
        geometry_mode, frame_width, frame_height, reused_data = (
            await _image_geometry(db, row, image, aspect_policy, action=action)
            if image and not needs_image
            else ("target", row.target_width, row.target_height, b"")
        )
        aspect_ratio = (
            desktop_aspect_ratio(frame_width, frame_height)
            if geometry_mode == "keep"
            else desktop_aspect_ratio(row.target_width, row.target_height)
        )
        if (
            image
            and not needs_image
            and aspect_policy == "expand"
            and not desktop_aspect_matches(frame_width, frame_height, row.target_width, row.target_height)
            and continuity == "base"
            and action.key != "idle"
        ):
            idle = await _shared_base_adjustment_action(db, row)
            if idle.generation_state_json and idle.stage != "complete":
                raise DesktopVideoStateError("请先完成或放弃安静相伴的制作草稿，再调整共享基础图")
            adjustment_action_id, adjustment_draft = idle.id, "none"
    if (
        image
        and not needs_image
        and aspect_policy == "expand"
        and not desktop_aspect_matches(frame_width, frame_height, row.target_width, row.target_height)
    ):
        return await generate_desktop_image(
            user_id,
            adjustment_action_id,
            feedback=feedback if adjustment_action_id == action_id else None,
            video_feedback=video_feedback if adjustment_action_id == action_id else None,
            expected_draft_id=adjustment_draft,
            expected_base_version=observed_base,
            expected_resolution_revision=observed_resolution,
            aspect_policy="expand",
        )
    if needs_image:
        return await generate_desktop_image(
            user_id,
            action_id,
            feedback=feedback,
            video_feedback=video_feedback,
            expected_draft_id=expected_draft_id,
            expected_base_version=expected_base_version,
            expected_resolution_revision=expected_resolution_revision,
            aspect_policy=aspect_policy,
            source=source,
            auto_continue=continue_automatically,
        )
    seconds, providers = (
        (duration, [])
        if resumable and frozen_chain
        else await resolve_desktop_video_plan(
            user_id,
            kind=kind,
            duration_seconds=duration,
            continuity=continuity,
            aspect_ratio=aspect_ratio,
            target_width=target_width,
            target_height=target_height,
            geometry_mode=geometry_mode,
            selection=video_selection,
        )
    )
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, action.set_id)
        state = await desktop_state(db, user_id, create=True)
        old = desktop_progress(action)
        if pending_update and (not action.enabled or not action.update_pending):
            return await desktop_action_response_with_sync(db, action)
        _validate_draft(
            row,
            old if action.stage != "complete" else None,
            draft_id=observed_draft,
            base_version=observed_base,
            resolution_revision=observed_resolution,
        )
        _validate_draft(
            row,
            old if action.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        if source == "autonomous" and (not state.autonomous_enabled or not SETTINGS.action_autocreate_enabled):
            raise DesktopVideoStateError("自主制作动作当前已关闭")
        if action.status == "processing" and action.stage != "paused":
            return await desktop_action_response_with_sync(db, action)
        if old is not None and desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先放弃本次制作再重新开始")
        if action.stage == "image_review":
            raise DesktopVideoStateError("请先确认图片后再生成视频")
        resume = (
            old is not None
            and old.image_confirmed
            and action.stage not in {"complete", "review"}
            and feedback == old.feedback
            and selected_video_feedback == old.video_feedback
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
            progress.paused_by_user = False
            progress.source = source
            progress.image_only = False
            progress.failure_reason = ""
        else:
            removed_task = desktop_removed_video_task(action)
            old_paths = asset_paths(action.generation_state_json, user_id)
            action.attempt += 1
            progress = DesktopVideoProgress(
                target_width=row.target_width,
                target_height=row.target_height,
                resolution_revision=row.resolution_revision,
                generation_id=uuid4().hex,
                source=source,
                feedback=feedback,
                video_feedback=selected_video_feedback,
                customization_priority=_customization_priority(old or accepted, feedback, video_feedback),
                action_description=desktop_action_description(action),
                creation_plan=old.creation_plan
                if old and old.feedback == feedback
                else accepted.creation_plan
                if accepted and accepted.feedback == feedback
                else None,
                opening_review=old.opening_review
                if old and old.feedback == feedback and old.pose_path == image
                else None,
                repair_count=confirmed_draft.repair_count
                if confirmed_draft
                and confirmed_draft.feedback == feedback
                and confirmed_draft.video_feedback == selected_video_feedback
                else 0,
                repair_reason=confirmed_draft.repair_reason
                if confirmed_draft
                and confirmed_draft.feedback == feedback
                and confirmed_draft.video_feedback == selected_video_feedback
                else "",
                repair_description=confirmed_draft.repair_description
                if confirmed_draft
                and confirmed_draft.feedback == feedback
                and confirmed_draft.video_feedback == selected_video_feedback
                else "",
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
                aspect_ratio=aspect_ratio,
                geometry_mode=geometry_mode,
                frame_width=frame_width,
                frame_height=frame_height,
                source_image_path=confirmed_draft.source_image_path
                if confirmed_draft
                else accepted.source_image_path
                if accepted
                else None,
                pose_path=image,
                image_origin=await desktop_image_origin(db, user_id, row.id, image),
                video_selection=video_selection,
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
        require_new_generation_call(user_id)
        action.duration_seconds = progress.duration_seconds
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error, action.update_pending = "processing", "video", None, False
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
    if not resume:
        cancel_desktop_video_job(action_id)
        spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    schedule_desktop_video_job(user_id, action_id)
    return response


async def _refinement_source(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    request: DesktopVideoRefineRequest,
    *,
    lock: bool = False,
) -> tuple[DesktopVideoAction, DesktopVideoSet, DesktopVideoAsset]:
    action = await desktop_action(db, user_id, action_id, lock=lock)
    row = await db.scalar(
        select(DesktopVideoSet).where(DesktopVideoSet.id == action.set_id, DesktopVideoSet.user_id == user_id),
    )
    if row is None:
        raise DesktopVideoNotFoundError("找不到对应的画面")
    progress = desktop_progress(action)
    _validate_draft(
        row,
        progress if action.stage != "complete" else None,
        draft_id=request.expected_draft_id or "none",
        base_version=request.expected_base_version,
        resolution_revision=request.expected_resolution_revision,
    )
    if action.version != request.expected_action_version:
        raise DesktopVideoStateError("当前视频已变化，请刷新后重试")
    if action.status == "processing" and action.stage != "paused":
        raise DesktopVideoStateError("这个桌面动作正在制作，请等待当前任务完成")
    if progress is not None and desktop_progress_has_unknown_submission(progress):
        raise DesktopVideoStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
    asset = (
        progress.candidate
        if request.source == "candidate" and progress is not None and action.stage == "review"
        else desktop_accepted_asset(action)
        if request.source == "current"
        else None
    )
    if asset is None:
        raise DesktopVideoStateError("要调整的视频已失效，请刷新后重试")
    if (
        asset.continuity == "base"
        and action.key != "idle"
        and (asset.base_version != row.base_version or asset.base_image_path != row.base_image_path)
    ):
        raise DesktopVideoStateError("基础画面已变化，请重新制作这个动作")
    return action, row, asset


async def _plan_refinement(
    user_id: int,
    row: DesktopVideoSet,
    asset: DesktopVideoAsset,
    request: DesktopVideoRefineRequest,
) -> tuple[int, int, int, list[FrozenMediaProvider]]:
    resolved = resolve_asset_reference(asset.video_path)
    if resolved is None:
        raise DesktopVideoStateError("参考视频缺失，请重新上传")
    try:
        data = await asyncio.to_thread(resolved[0].read_bytes)
    except OSError as exc:
        raise DesktopVideoStateError("参考视频无法读取，请重新上传") from exc
    width, height = (
        (row.target_width, row.target_height) if request.aspect_policy == "expand" else (asset.width, asset.height)
    )
    if (
        request.aspect_policy == "expand"
        and not request.feedback.strip()
        and can_crop_to_aspect(asset.width, asset.height, width, height)
    ):
        return (asset.duration_ms + 999) // 1000, width, height, []
    seconds, providers = await plan_desktop_video_refinement(
        user_id,
        data,
        resolved[0].suffix,
        aspect_ratio=desktop_aspect_ratio(width, height),
        target_width=row.target_width,
        target_height=row.target_height,
        geometry_mode="target" if request.aspect_policy == "expand" else "keep",
        selection=ProviderSelection(origin=asset.video_origin, mode="current"),
        feedback=request.feedback.strip(),
    )
    return seconds, width, height, providers


def _refinement_progress(
    action: DesktopVideoAction,
    row: DesktopVideoSet,
    refinement: DesktopVideoRefinementInput,
    *,
    seconds: int,
    providers: list[FrozenMediaProvider],
    feedback: str = "",
    aspect_policy: str = "expand",
    asset: DesktopVideoAsset | None = None,
) -> DesktopVideoProgress:
    width, height = (
        (row.target_width, row.target_height) if aspect_policy == "expand" else (refinement.width, refinement.height)
    )
    return DesktopVideoProgress(
        generation_id=uuid4().hex,
        target_width=row.target_width,
        target_height=row.target_height,
        resolution_revision=row.resolution_revision,
        source="user_requested",
        feedback=asset.feedback if asset else "",
        video_feedback=feedback.strip() or (asset.video_feedback if asset else ""),
        refinement_feedback=feedback.strip(),
        customization_priority="video" if feedback.strip() else asset.customization_priority if asset else "general",
        action_description=desktop_action_description(action),
        creation_plan=asset.creation_plan if asset else None,
        duration_seconds=seconds,
        video_refinement=refinement,
        video_selection=ProviderSelection(origin=refinement.video_origin, mode="current"),
        video_chain_json=MediaChainState(providers=providers).model_dump_json(),
        geometry_mode="target" if aspect_policy == "expand" else "keep",
        aspect_ratio=desktop_aspect_ratio(width, height),
        frame_width=width,
        frame_height=height,
        image_confirmed=True,
        force_review=True,
        pose_path=refinement.image_path,
        continuity=asset.continuity if asset else _effective_continuity(action),
        base_version=(row.base_version + 1 if action.key == "idle" else row.base_version)
        if (asset.continuity if asset else _effective_continuity(action)) == "base"
        else None,
        base_image_path=row.base_image_path
        if (asset.continuity if asset else _effective_continuity(action)) == "base" and action.key != "idle"
        else None,
        replace_base=action.key == "idle",
    )


async def preview_desktop_video_refinement(
    user_id: int,
    action_id: int,
    request: DesktopVideoRefineRequest,
) -> DesktopVideoRefinePreviewResponse:
    async with SESSION_LOCAL() as db:
        _action, row, asset = await _refinement_source(db, user_id, action_id, request)
    seconds, width, height, _providers = await _plan_refinement(user_id, row, asset, request)
    return DesktopVideoRefinePreviewResponse(duration_seconds=seconds, width=width, height=height)


async def refine_desktop_video(
    user_id: int,
    action_id: int,
    request: DesktopVideoRefineRequest,
) -> DesktopVideoActionResponse:
    if not request.feedback.strip() and request.aspect_policy != "expand":
        raise DesktopVideoStateError("请填写视频调整要求或选择适配桌景画幅")
    async with SESSION_LOCAL() as db:
        _action, row, asset = await _refinement_source(db, user_id, action_id, request)
    seconds, _width, _height, providers = await _plan_refinement(user_id, row, asset, request)
    local_fit = not providers
    async with _lock(user_id), SESSION_LOCAL() as db:
        action, row, asset = await _refinement_source(db, user_id, action_id, request, lock=True)
        if not local_fit:
            require_new_generation_call(user_id)
        removed_task = desktop_removed_video_task(action)
        old_paths = asset_paths(action.generation_state_json, user_id)
        progress = _refinement_progress(
            action,
            row,
            DesktopVideoRefinementInput(
                video_path=asset.video_path,
                original_video_path=asset.source_video_path or asset.video_path,
                image_path=asset.image_path,
                width=asset.width,
                height=asset.height,
                duration_ms=asset.duration_ms,
                video_origin=asset.video_origin,
            ),
            seconds=seconds,
            providers=providers,
            feedback=request.feedback,
            aspect_policy=request.aspect_policy,
            asset=asset,
        )
        progress.source_image_path = asset.source_image_path
        if local_fit:
            progress.source = "external_upload" if asset.source == "external_upload" else "user_requested"
            progress.source_path = asset.video_path
            progress.reprocess_only = True
        action.attempt += 1
        action.duration_seconds = seconds
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error, action.update_pending = (
            "processing",
            "process" if local_fit else "video",
            None,
            False,
        )
        await enqueue_asset_cleanup(db, user_id, old_paths - asset_paths(action.generation_state_json, user_id))
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
    cancel_desktop_video_job(action_id)
    spawn_removed_action_task_cancellation(user_id, [removed_task] if removed_task else [])
    schedule_desktop_video_job(user_id, action_id)
    return response


def _uploaded_refinement_frame(data: bytes, extension: str) -> tuple[bytes, int]:
    with TemporaryDirectory(prefix="desktop-reference-") as directory:
        source = Path(directory) / f"source.{extension}"
        source.write_bytes(data)
        return extract_desktop_first_frame(source), round(probe_video(source).duration_seconds * 1000)


async def upload_desktop_action(
    user_id: int,
    action_id: int,
    data: bytes,
    extension: str,
    *,
    expected_action_version: int,
    expected_set_id: int | None = None,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    aspect_policy: str = "ask",
) -> DesktopVideoActionResponse:
    """保留上传字节；原样交付与参考原片制作共用草稿替换和资产管理。"""
    if not data:
        raise DesktopVideoStateError("上传的视频文件为空")
    extension = extension.lower().lstrip(".")
    detected = sniff_media_ext(data)
    if extension not in {"mp4", "mov"} or detected not in {"mp4", "mov"}:
        raise DesktopVideoStateError("仅支持 MP4 或 MOV 视频")
    try:
        width, height = await asyncio.to_thread(probe_desktop_video, data, extension)
    except (ValueError, OSError, VideoProcessError) as exc:
        raise DesktopVideoStateError("视频无法读取，请上传完整的视频") from exc
    async with SESSION_LOCAL() as db:
        initial = await desktop_action(db, user_id, action_id)
        if initial.version != expected_action_version:
            raise DesktopVideoStateError("当前成品已变化，请刷新后重试")
        if expected_set_id is not None and expected_set_id != initial.set_id:
            raise DesktopVideoStateError("桌面生活组合已经变化")
        if initial.status == "processing" and initial.stage != "paused":
            raise DesktopVideoStateError("这个桌面动作正在制作，请等待当前任务完成")
        row = await db.scalar(
            select(DesktopVideoSet).where(DesktopVideoSet.id == initial.set_id, DesktopVideoSet.user_id == user_id),
        )
        if row is None:
            raise DesktopVideoNotFoundError("找不到对应的画面")
        _validate_draft(
            row,
            desktop_progress(initial) if initial.stage != "complete" else None,
            draft_id=expected_draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
        old = desktop_progress(initial)
        if old is not None and desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        observed_draft = old.generation_id if old is not None and initial.stage != "complete" else "none"
        observed_version = initial.version
        observed_base, observed_resolution = row.base_version, row.resolution_revision
    target_ratio = desktop_aspect_ratio(row.target_width, row.target_height)
    local_fit = can_crop_to_aspect(width, height, row.target_width, row.target_height)
    try:
        geometry_mode = _geometry_mode(row, width, height, aspect_policy, "video")
    except DesktopVideoAspectRatioError as exc:
        try:
            if local_fit:
                _frame, duration_ms = await asyncio.to_thread(_uploaded_refinement_frame, data, extension)
                seconds = (duration_ms + 999) // 1000
            else:
                seconds, _providers = await plan_desktop_video_refinement(
                    user_id,
                    data,
                    extension,
                    aspect_ratio=target_ratio,
                    target_width=row.target_width,
                    target_height=row.target_height,
                    geometry_mode="target",
                )
            exc.details["duration_seconds"] = seconds
        except DesktopVideoError as planning_error:
            exc.details["adjustment_error"] = str(planning_error)
        except Exception:
            logger.warning(
                "Desktop video adjustment preview unavailable",
                extra={"action_id": action_id},
                exc_info=True,
            )
            exc.details["adjustment_error"] = "视频调整暂时不可用，请稍后重试"
        raise
    seconds = initial.duration_seconds
    providers: list[FrozenMediaProvider] = []
    source_frame: bytes | None = None
    source_duration_ms = 0
    ai_adjustment = aspect_policy == "expand" and not local_fit
    if ai_adjustment:
        seconds, providers = await plan_desktop_video_refinement(
            user_id,
            data,
            extension,
            aspect_ratio=target_ratio,
            target_width=row.target_width,
            target_height=row.target_height,
            geometry_mode="target",
        )
        source_frame, source_duration_ms = await asyncio.to_thread(_uploaded_refinement_frame, data, extension)
    async with _lock(user_id), SESSION_LOCAL() as db:
        initial = await desktop_action(db, user_id, action_id, lock=True)
        row = await _suite(db, user_id, initial.set_id)
        old = desktop_progress(initial)
        _validate_draft(
            row,
            old if initial.stage != "complete" else None,
            draft_id=observed_draft,
            base_version=observed_base,
            resolution_revision=observed_resolution,
        )
        if initial.version != observed_version:
            raise DesktopVideoStateError("当前视频已变化，请刷新后重试")
        if initial.status == "processing" and initial.stage != "paused":
            raise DesktopVideoStateError("这个桌面动作正在制作，请等待当前任务完成")
        if old is not None and desktop_progress_has_unknown_submission(old):
            raise DesktopVideoStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        if ai_adjustment:
            require_new_generation_call(user_id)
        initial.attempt += 1
        generation_id = uuid4().hex
        directory = f"desktop/{initial.set_id}/{initial.id}/{generation_id}"
        old_paths = asset_paths(initial.generation_state_json, user_id)
        removed_task = desktop_removed_video_task(initial)
        previous = desktop_accepted_asset(initial)
        if previous is not None:
            old_paths -= {previous.video_path, previous.poster_path}
        progress = DesktopVideoProgress(
            target_width=row.target_width,
            target_height=row.target_height,
            resolution_revision=row.resolution_revision,
            aspect_ratio=target_ratio if geometry_mode == "target" else desktop_aspect_ratio(width, height),
            generation_id=generation_id,
            source="external_upload",
            geometry_mode=geometry_mode,
            frame_width=width,
            frame_height=height,
            duration_seconds=seconds,
            continuity="base" if initial.key == "idle" else "independent",
            base_version=(row.base_version + 1 if initial.key == "idle" else row.base_version)
            if initial.key == "idle"
            else None,
            base_image_path=None,
            replace_base=initial.key == "idle",
            video_chain_json=MediaChainState(providers=providers).model_dump_json(),
            force_review=aspect_policy == "expand",
            reprocess_only=aspect_policy == "expand" and local_fit,
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
    source_image_path: str | None = None
    try:
        source_path = await save_action_source_asset_async(
            data,
            user_id=user_id,
            generation_id=generation_id,
            attempt=initial.attempt,
            ext=detected,
            directory=directory,
        )
        if source_frame is not None:
            source_image_path = await save_companion_asset_async(
                source_frame,
                user_id=user_id,
                label="desktop_reference",
                ext="png",
                directory=directory,
            )
        async with SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id, lock=True)
            current = desktop_progress(action)
            if current is None or current.generation_id != generation_id or action.status != "processing":
                raise DesktopVideoStateError("上传任务已经失效")
            if source_image_path is not None:
                row = await _suite(db, user_id, action.set_id)
                updated = _refinement_progress(
                    action,
                    row,
                    DesktopVideoRefinementInput(
                        video_path=source_path,
                        original_video_path=source_path,
                        image_path=source_image_path,
                        width=width,
                        height=height,
                        duration_ms=source_duration_ms,
                    ),
                    seconds=seconds,
                    providers=providers,
                )
                updated.generation_id = current.generation_id
                updated.paused_by_user = current.paused_by_user
                current = updated
                action.duration_seconds = seconds
            else:
                current.source_path = source_path
            action.generation_state_json = current.model_dump_json()
            action.stage = "paused" if current.paused_by_user else "video" if current.video_refinement else "process"
            await touch_desktop_state(db, user_id)
            await db.commit()
    except (Exception, asyncio.CancelledError) as exc:
        recovered = False
        async with SESSION_LOCAL() as db:
            await desktop_state(db, user_id, lock=True)
            # 提交结果可能不确定；回收任务核对持久引用后再删除原片和参考帧。
            await enqueue_asset_cleanup(db, user_id, {path for path in (source_path, source_image_path) if path})
            saved_action = await db.scalar(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.id == action_id,
                    DesktopVideoAction.user_id == user_id,
                ),
            )
            current = desktop_progress(saved_action) if saved_action else None
            if saved_action is not None and current is not None and current.generation_id == generation_id:
                refinement = current.video_refinement
                if source_path and (
                    current.source_path == source_path
                    or refinement is not None
                    and refinement.video_path == source_path
                    and refinement.image_path == source_image_path
                ):
                    action, recovered = saved_action, True
                else:
                    current.failure_reason = "failed"
                    saved_action.generation_state_json = current.model_dump_json()
                    saved_action.status, saved_action.stage, saved_action.error = (
                        "failed",
                        "upload",
                        "上传视频保存失败，请重试",
                    )
                    await touch_desktop_state(db, user_id)
            await db.commit()
        if saved_action is None:
            cleanup_asset_directories_after_task(user_id, [directory])
        if isinstance(exc, asyncio.CancelledError):
            raise
        if not recovered:
            if isinstance(exc, DesktopVideoError):
                raise
            raise DesktopVideoStateError("上传视频保存失败，请重试") from exc
    if action.status == "processing" and current is not None and not current.paused_by_user:
        schedule_desktop_video_job(user_id, action_id)
    async with SESSION_LOCAL() as db:
        return await get_desktop_action_response(db, user_id, action_id)


async def review_desktop_action(
    user_id: int,
    action_id: int,
    *,
    accepted: bool,
    draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
) -> DesktopVideoActionResponse:
    removed_tasks: list[RemovedActionVideoTask] = []
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id, lock=True)
        progress = desktop_progress(action)
        row = await _suite(db, user_id, action.set_id)
        if action.stage == "complete" and progress and draft_id == progress.generation_id:
            if not accepted:
                raise DesktopVideoStateError("该视频已被采用，不能拒绝同一草稿")
            return await desktop_action_response_with_sync(db, action)
        _validate_draft(
            row,
            progress,
            draft_id=draft_id,
            base_version=expected_base_version,
            resolution_revision=expected_resolution_revision,
        )
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
            action.status = (
                "ready"
                if action.accepted_asset_json
                else "not_generated"
                if action.definition_id is not None
                else "queued"
            )
            action.stage, action.error = "complete" if action.accepted_asset_json else "prepare", None
            await touch_desktop_state(db, user_id)
        await db.commit()
        response = await desktop_action_response_with_sync(db, action)
        set_id = action.set_id
    if accepted:
        spawn_removed_action_task_cancellation(user_id, removed_tasks)
        await notify_desktop_set_updated(user_id, set_id)
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
            geometry_mode=progress.geometry_mode if progress else "target",
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


async def design_desktop_action(
    user_id: int,
    request: DesktopVideoDesignRequest,
    *,
    source: str = "user_requested",
    require_desktop: bool = False,
) -> DesktopVideoProposalResponse:
    return await design_shared_desktop_action(
        user_id,
        request,
        source=source,
        require_desktop=require_desktop,
    )


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
                "definition_id": action.definition_id,
                "name": action.name,
                "description": action.description,
                "status": action.status,
                "stage": action.stage,
                "error": action.error,
                "sync_targets": [target.model_dump() for target in action.sync_targets],
            }
            for action in actions
            if action.status != "ready"
        ],
    }
    payload["proposals"] = [
        {
            "proposal_id": proposal.proposal_id,
            "definition_id": proposal.definition_id,
            "name": proposal.name,
            "description": proposal.description,
            "status": proposal.status,
            "action_id": proposal.action_id,
            "action_status": next(
                (
                    target.status
                    for target in proposal.sync_targets
                    if target.target_kind == "desktop"
                    and state.current is not None
                    and target.target_id == state.current.id
                ),
                None,
            ),
            "sync_targets": [target.model_dump() for target in proposal.sync_targets],
            "reason": proposal.review_reason,
        }
        for proposal in (state.current.proposals if state.current else [])[:5]
    ]
    payload["proposals_truncated"] = bool(state.current and len(state.current.proposals) > 5)
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
                    "description": desktop_asset_description(stored_action, asset),
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
