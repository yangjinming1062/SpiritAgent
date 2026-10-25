"""影像制作流水线：图片链、图片检查、运动设计、视频队列与发布，进程内按工作项单飞。"""

import asyncio
import hashlib
import json
from fractions import Fraction
from io import BytesIO
from typing import Literal, NamedTuple

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.llm_client import build_provider, resolve_provider_chain, resolve_vision_chain
from infrastructure.ai.prompt_engineer import VisualReasoningError, vision_chat_json
from infrastructure.ai.providers.base import ServiceType, VideoGenProvider, VideoGenRequest
from infrastructure.ai.selection import ProviderSelection, has_provider_selection, order_provider_chain
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import (
    build_data_uri,
    read_valid_image_data_uri,
    resolve_asset_reference,
    save_companion_asset_async,
    save_video_job_asset_async,
    validate_image_bytes,
)
from infrastructure.assets.media_download import download_media_result
from infrastructure.assets.references import asset_paths
from infrastructure.async_io import cancel_and_wait, cancel_tasks, run_blocking
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.media_geometry import MAX_DESKTOP_CROP_FRACTION, centered_crop_box, crop_fraction
from infrastructure.text import resolve_language, utc_now
from infrastructure.video_processing.desktop import prepare_desktop_video, sample_desktop_video_frames
from infrastructure.video_processing.ffmpeg import VideoProcessError, VideoToolUnavailableError
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import track_user_task
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from features.appearance.character_card import render_character_identity
from features.appearance.visual_identity import align_character_reference
from features.assets.lifecycle import cleanup_asset_directories_after_task
from features.companion.proactive_runtime import get_presentation_snapshot
from features.configuration.settings.values import get_user_setting
from features.jobs.service import bind_job_assets
from features.life.schemas import LifeEventInput
from features.media.chain import (
    MEDIA_IDENTITY_ACCEPT_SCORE,
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
    video_failure_message,
    video_provider_failure_reason,
)
from features.media.character_images import (
    CharacterImageInput,
    ImageChainState,
    generate_character_images,
)
from features.media.identity_review import score_character_frames_with_references
from features.media.image_generation import ImageGenerationError, ImageReviewUnavailableError
from features.media.video_queue.contracts import VideoQueueInfo, VideoQueueOwner, VideoQueueStoppedError
from features.media.video_queue.service import (
    acknowledge_video_attempt,
    cancel_video_attempt,
    enqueue_video_attempt,
    pause_video_attempt,
    wait_video_attempt,
)

from .creation import (
    VisualCreationError,
    begin_opening_repair,
    ensure_creation_plan,
    ensure_opening_repair,
    opening_image_hash,
    opening_passed,
    opening_repair_reference,
    opening_requirements,
    opening_requirements_hash,
    review_existing_opening,
)
from .geometry import probe_visual_image, visual_aspect_matches, visual_aspect_ratio
from .life import settle_visual_steps
from .models import VisualJob
from .naming import schedule_visual_title
from .ports import notify_visual_ready
from .prompts import (
    VISUAL_MOTION_DESIGN,
    VISUAL_POSE_EDIT_TEMPLATE,
    VISUAL_POSE_IMAGE_TEMPLATE,
    VISUAL_POSE_VARIANT_TEMPLATE,
    VISUAL_VIDEO_FRAME_DESCRIPTION,
    VISUAL_VIDEO_LOOP,
    VISUAL_VIDEO_MOTION_TEMPLATE,
    VISUAL_VIDEO_ONCE,
    VISUAL_VIDEO_REVIEW,
    VISUAL_VIDEO_SCORE,
    visual_source_reference_prompt,
)
from .publication import PublishedVisual, publish_opening_result, publish_visual_result
from .repository import (
    VisualStateError,
    dispatch_play_command,
    notify_visuals_updated,
    publication_rows,
    visual_job_row,
    visual_progress,
    visual_snapshot,
)
from .schemas import (
    ComboSnapshot,
    OpeningReview,
    VisualAsset,
    VisualJobProgress,
)
from .video_planning import (
    VisualVideoPlan,
    VisualVideoResolution,
    freeze_visual_video_plans,
    nearest_visual_video_resolution,
)

logger = get_logger(__name__)
_JOBS: dict[int, asyncio.Task[None]] = {}
_FINISHING_JOBS: dict[int, set[asyncio.Task[None]]] = {}
_NEXT_JOBS: dict[int, tuple[int, bool]] = {}


class VisualMotionScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feasible: bool = Field(strict=True)
    reason: str = Field(max_length=200)
    motion_prompt: str = Field(max_length=2000)
    visual_description: str = Field(max_length=1500)
    outfit_description: str = Field(max_length=1200)
    failure_kind: Literal["image", "duration", "unsupported", None] = None
    minimum_duration_seconds: int | None = Field(default=None, gt=0, le=600, strict=True)

    @model_validator(mode="after")
    def valid_motion(self) -> "VisualMotionScript":
        if self.feasible:
            if (
                not self.motion_prompt.strip()
                or self.failure_kind is not None
                or self.minimum_duration_seconds is not None
            ):
                raise ValueError("可实现的活动须提供运动描述，失败类型与所需时长须为空")
        elif (
            self.motion_prompt.strip()
            or self.failure_kind is None
            or (self.failure_kind == "duration") != (self.minimum_duration_seconds is not None)
        ):
            raise ValueError("不可实现的活动须说明失败类型，仅时长不足时提供所需时长，运动描述须为空")
        return self


class VisualVideoFrameDescription(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: str = Field(max_length=1500)
    outfit_description: str = Field(max_length=1200)


class VisualVideoReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "review"]
    reason: str = Field(max_length=500)


class VisualMotionError(VisualCreationError):
    def __init__(self, script: VisualMotionScript) -> None:
        super().__init__(
            script.reason or "当前画面无法完成这个活动",
            "activity_unsupported" if script.failure_kind == "unsupported" else "image_unsuitable",
        )
        self.script = script


class VisualCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: VisualAsset
    verdict: str
    reason: str


def visual_progress_has_unknown_submission(progress: VisualJobProgress) -> bool:
    if progress.submission_unknown:
        return True
    for chain_json in (progress.reference_alignment_chain_json, progress.image_chain_json):
        if not chain_json:
            continue
        image = ImageChainState.model_validate_json(chain_json)
        if image.phase == "submitting" or image.stop_reason == "result_unknown":
            return True
    if progress.video_chain_json:
        video = MediaChainState.model_validate_json(progress.video_chain_json)
        if video.phase == "submitting" and not video.queue_request_id:
            return True
    return False


def _queued_video_request(progress: VisualJobProgress) -> str | None:
    if not progress.video_chain_json:
        return None
    return MediaChainState.model_validate_json(progress.video_chain_json).queue_request_id


def _unknown_submission_message(progress: VisualJobProgress) -> str:
    alignment = (
        ImageChainState.model_validate_json(progress.reference_alignment_chain_json)
        if progress.reference_alignment_chain_json
        else None
    )
    if (
        progress.image_only
        or alignment is not None
        and (alignment.phase == "submitting" or alignment.stop_reason == "result_unknown")
    ):
        return "图片提交结果不确定，请先核对结果或放弃本次制作，系统没有自动重试"
    return video_failure_message("result_unknown")


def visual_image_chain_exhausted(progress: VisualJobProgress) -> bool:
    if not progress.image_chain_json or visual_progress_has_unknown_submission(progress):
        return False
    image = ImageChainState.model_validate_json(progress.image_chain_json)
    return bool(
        image.providers
        and image.phase in {"ready", "storing", "evaluating", "complete"}
        and (image.stop_reason or image.next_index >= len(image.providers))
        and not (image.candidates or image.pending_urls or image.pending_path),
    )


def visual_progress_has_video_work(progress: VisualJobProgress) -> bool:
    chain = MediaChainState.model_validate_json(progress.video_chain_json) if progress.video_chain_json else None
    return bool(
        (chain is not None and chain.result_url)
        or progress.source_path
        or progress.candidate
        or _queued_video_request(progress),
    )


def visual_progress_can_resume(progress: VisualJobProgress) -> bool:
    if visual_progress_has_unknown_submission(progress) or progress.failure_reason in {
        "material_rejected",
        "queue_stopped",
        "quality_failed",
    }:
        return False
    if visual_progress_has_video_work(progress):
        return True
    if progress.video_chain_json:
        video = MediaChainState.model_validate_json(progress.video_chain_json)
        if video.providers and video.phase == "ready" and video.next_index >= len(video.providers):
            return False
    if progress.pose_path:
        return True
    if progress.image_chain_json:
        image = ImageChainState.model_validate_json(progress.image_chain_json)
        return bool(
            image.candidates
            or image.pending_urls
            or image.pending_path
            or image.inputs is not None
            and image.phase == "ready"
            and not image.stop_reason
            and image.next_index < len(image.providers),
        )
    if progress.aligned_outfit_path:
        return True
    if progress.reference_alignment_chain_json:
        alignment = ImageChainState.model_validate_json(progress.reference_alignment_chain_json)
        return alignment.can_retry_goal_review() or bool(
            alignment.best()
            or alignment.pending_urls
            or alignment.pending_path
            or alignment.inputs is None
            and not alignment.stop_reason
            or alignment.inputs is not None
            and alignment.phase == "ready"
            and not alignment.stop_reason
            and alignment.next_index < len(alignment.providers),
        )
    return progress.creation_plan is not None or bool(progress.previous_pose_path)


class JobInputs(NamedTuple):
    job: VisualJob
    snapshot: ComboSnapshot
    progress: VisualJobProgress


async def _job_inputs(user_id: int, job_id: int, generation_id: str) -> JobInputs:
    async with SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        progress = visual_progress(job)
        if job.purpose == "regenerate" and job.target_visual_id is None and job.state in {"queued", "running"}:
            job.state, job.stage, job.error = "cancelled", "cancelled", "重做目标已删除，制作已停止"
            await settle_visual_steps(db, user_id, job)
            await notify_visuals_updated(db, user_id)
            await db.commit()
            raise LlmCallBlockedError("影像重做目标已删除")
        if progress is None or progress.generation_id != generation_id or job.state not in {"queued", "running"}:
            raise LlmCallBlockedError("影像制作任务已经失效")
        snapshot = visual_snapshot(job)
        if snapshot.identity.avatar_id is None:
            raise LlmCallBlockedError("制作引用的角色资料已删除")
        db.expunge(job)
        return JobInputs(job=job, snapshot=snapshot, progress=progress)


async def _require_active_job(user_id: int, job_id: int, generation_id: str) -> None:
    require_new_generation_call(user_id)
    await _job_inputs(user_id, job_id, generation_id)


async def _save_progress(
    user_id: int,
    job_id: int,
    progress: VisualJobProgress,
    *,
    stage: str,
    repair_count: int | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        saved = visual_progress(job)
        if saved is None or saved.generation_id != progress.generation_id or job.state not in {"queued", "running"}:
            raise LlmCallBlockedError("影像制作任务已经失效")
        checkpoint = progress.model_dump(mode="json")
        paths = asset_paths([job.snapshot_json, checkpoint], user_id)
        present = {
            path for path in paths if (resolved := resolve_asset_reference(path)) is not None and resolved[0].is_file()
        }
        await bind_job_assets(db, user_id, job.id, present, "dependency")
        job.checkpoint = checkpoint
        job.stage = stage
        if repair_count is not None:
            job.repair_count = repair_count
        await notify_visuals_updated(db, user_id)
        await db.commit()


async def _references(snapshot: ComboSnapshot) -> tuple[str, str]:
    portrait = await asyncio.to_thread(read_valid_image_data_uri, snapshot.primary_reference_path)
    outfit = await asyncio.to_thread(read_valid_image_data_uri, snapshot.outfit.path)
    if not portrait or not outfit:
        raise VisualStateError("组合的参考图片无法读取")
    return portrait, outfit


def _alignment_source_hash(snapshot: ComboSnapshot) -> str:
    source = {
        "identity_hash": snapshot.identity_hash,
        "identity": snapshot.identity.model_dump(mode="json", exclude={"avatar_id"}),
        "outfit_hash": snapshot.outfit.content_hash,
        "outfit_description": snapshot.outfit.description,
        "identity_reference_role": snapshot.identity_reference_role,
    }
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def _aligned_outfit_reference(
    user_id: int,
    job_id: int,
    snapshot: ComboSnapshot,
    progress: VisualJobProgress,
    directory: str,
    identity_uri: str,
    outfit_uri: str,
) -> str:
    source_hash = _alignment_source_hash(snapshot)
    if progress.reference_alignment_source_hash is None:
        progress.reference_alignment_source_hash = source_hash
    elif progress.reference_alignment_source_hash != source_hash:
        raise VisualStateError("组合参考对齐的冻结来源已变化")
    state = ImageChainState.model_validate_json(progress.reference_alignment_chain_json or "{}")
    if state.phase == "submitting" or state.stop_reason == "result_unknown":
        raise ImageGenerationError(_unknown_submission_message(progress), result_unknown=True)
    if state.inputs is not None and (
        not isinstance(state.inputs, CharacterImageInput) or state.inputs.goal_review != "required"
    ):
        raise VisualStateError("组合参考对齐缺少画面检查约束")
    if progress.aligned_outfit_path:
        selected = state.best()
        if state.phase != "complete" or selected is None or selected.path != progress.aligned_outfit_path:
            raise VisualStateError("派生造型参考缺少已通过的对齐结果")
        reference = await asyncio.to_thread(read_valid_image_data_uri, progress.aligned_outfit_path)
        if not reference:
            raise VisualStateError("派生造型参考无法读取")
        return reference

    async def save_alignment(value: ImageChainState) -> None:
        progress.reference_alignment_chain_json = value.model_dump_json()
        await _save_progress(user_id, job_id, progress, stage="image")

    async def before_submit() -> None:
        await _require_active_job(user_id, job_id, progress.generation_id)

    await save_alignment(state)
    path = await align_character_reference(
        user_id,
        outfit_uri,
        snapshot.identity,
        snapshot.outfit.description,
        task=AiTaskKey.visuals,
        identity_reference=identity_uri,
        storage_directory=directory,
        state=state,
        save_progress=save_alignment,
        before_submit=before_submit,
        selection=progress.image_selection,
    )
    progress.aligned_outfit_path = path
    resolved = resolve_asset_reference(path)
    progress.aligned_outfit_hash = (
        hashlib.sha256(resolved[0].read_bytes()).hexdigest() if resolved is not None else None
    )
    await _save_progress(user_id, job_id, progress, stage="image")
    reference = await asyncio.to_thread(read_valid_image_data_uri, path)
    if not reference:
        raise VisualStateError("派生造型参考无法读取")
    return reference


def _frame_bytes(
    data: bytes,
    ratio: str | None = None,
    pixel_bounds: tuple[int, int] | None = None,
) -> tuple[bytes, int, int]:
    validate_image_bytes(data)
    with Image.open(BytesIO(data)) as image:
        ImageOps.exif_transpose(image, in_place=True)
        with image.convert("RGB") as rgb, BytesIO() as output:
            width, height = rgb.size
            box = (0, 0, width, height)
            if ratio:
                left, right = (int(value) for value in ratio.split(":"))
                try:
                    box = centered_crop_box(width, height, left, right)
                except ValueError as exc:
                    raise VisualStateError("图片画幅差异超过少量裁切范围，请先适配图片") from exc
                width, height = box[2] - box[0], box[3] - box[1]
            with rgb.crop(box) as fitted:
                if pixel_bounds is not None:
                    minimum, maximum = pixel_bounds
                    scale = min(max(1.0, minimum / width, minimum / height), maximum / width, maximum / height)
                    target_size = (max(1, round(width * scale)), max(1, round(height * scale)))
                    with fitted.resize(target_size, Image.Resampling.LANCZOS) as resized:
                        resized.save(output, format="PNG")
                    width, height = target_size
                else:
                    fitted.save(output, format="PNG")
            return output.getvalue(), width, height


def _provider_ratio(provider: VideoGenProvider, source_ratio: str) -> str | None:
    if provider.supports_adaptive_aspect_ratio:
        bounds = provider.adaptive_aspect_ratio_range
        if bounds is None:
            return source_ratio
        value = Fraction(source_ratio.replace(":", "/"))
        lower, upper = (Fraction(str(bound)).limit_denominator(1000) for bound in bounds)
        return source_ratio if lower <= value <= upper else None
    target = Fraction(source_ratio.replace(":", "/"))
    ratios: list[tuple[float, str]] = []
    for ratio in provider.aspect_ratios:
        native = Fraction(ratio.replace(":", "/"))
        loss = crop_fraction(native.numerator, native.denominator, target.numerator, target.denominator)
        if loss <= MAX_DESKTOP_CROP_FRACTION + 1e-9:
            ratios.append((loss, ratio))
    return min(ratios, key=lambda item: item[0])[1] if ratios else None


def _delivery_aspect(progress: VisualJobProgress) -> str:
    return visual_aspect_ratio(progress.target_width, progress.target_height)


async def _prepare_geometry(progress: VisualJobProgress, data: bytes) -> tuple[bytes, int, int]:
    normalized, width, height = await asyncio.to_thread(_frame_bytes, data)
    progress.aspect_ratio = _delivery_aspect(progress)
    progress.frame_width, progress.frame_height = width, height
    return normalized, width, height


async def resolve_visual_video_plan(
    user_id: int,
    *,
    clip_kind: str,
    duration_seconds: int,
    aspect_ratio: str,
    target_width: int,
    target_height: int,
    selection: ProviderSelection | None = None,
) -> tuple[int, list[FrozenMediaProvider]]:
    needs_last_frame = clip_kind == "loop"
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.visuals)
        if not await resolve_vision_chain(db, user_id, task=AiTaskKey.visuals):
            raise VisualStateError("请先配置视觉模型")

    def resolution_for(provider: VideoGenProvider, seconds: int, ratio: str) -> VisualVideoResolution | None:
        return nearest_visual_video_resolution(
            provider,
            duration=seconds,
            native_ratio=ratio,
            delivery_ratio=aspect_ratio,
            target_width=target_width,
            target_height=target_height,
            first_frame=True,
            last_frame=needs_last_frame,
        )

    plans: list[VisualVideoPlan] = []
    for config in order_provider_chain(chain, selection):
        provider = build_provider(config, VideoGenProvider)
        if not provider.supports_first_frame or (needs_last_frame and not provider.supports_loop_frames):
            continue
        ratio = _provider_ratio(provider, aspect_ratio)
        if ratio is None:
            continue
        seconds = next(
            (
                value
                for value in sorted(provider.durations or (duration_seconds,))
                if duration_seconds <= value <= 15
                and provider.available_resolutions(duration=value, first_frame=True, last_frame=needs_last_frame)
            ),
            None,
        )
        if seconds is None:
            continue
        choice = resolution_for(provider, seconds, ratio)
        if choice is not None:
            plans.append(VisualVideoPlan(provider, ratio, seconds, choice))
    result = freeze_visual_video_plans(
        plans,
        preserve_order=has_provider_selection(chain, selection),
        resolution_for=resolution_for,
    )
    if result is not None:
        return result
    raise VisualStateError(
        f"当前视频模型均不支持 {aspect_ratio} 画幅及所需时长、首帧条件；请选择支持该画幅的模型，循环动作还需支持首尾帧",
    )


async def _prepare_pose(
    user_id: int,
    job: VisualJob,
    snapshot: ComboSnapshot,
    progress: VisualJobProgress,
    directory: str,
) -> None:
    if progress.pose_path:
        return

    async def before_submit() -> None:
        await _require_active_job(user_id, job.id, progress.generation_id)

    previous = progress.previous_pose_path
    progress.frame_width, progress.frame_height = progress.target_width, progress.target_height
    progress.aspect_ratio = _delivery_aspect(progress)
    editing = bool(previous)
    if editing:
        reference = await asyncio.to_thread(read_valid_image_data_uri, previous)
        if not reference:
            raise VisualStateError("要调整的画面无法读取")
        portrait, outfit = reference, None
        template = VISUAL_POSE_VARIANT_TEMPLATE if progress.regenerate_image else VISUAL_POSE_EDIT_TEMPLATE
        prompt = template.format(
            feedback=opening_requirements(job, progress),
            aspect_ratio=progress.aspect_ratio,
        )
    else:
        portrait, outfit = await _references(snapshot)
        if snapshot.identity_reference_role == "fullbody" and not progress.image_chain_json:
            outfit = await _aligned_outfit_reference(
                user_id,
                job.id,
                snapshot,
                progress,
                directory,
                portrait,
                outfit,
            )
        prompt = VISUAL_POSE_IMAGE_TEMPLATE.format(
            references=visual_source_reference_prompt(snapshot.identity_reference_role, include_scene=True),
            pose=opening_requirements(job, progress),
            environment=snapshot.scene.description,
            outfit=snapshot.outfit.description,
            identity=render_character_identity(snapshot.identity),
            aspect_ratio=progress.aspect_ratio,
        )
    state = (
        ImageChainState.model_validate_json(progress.image_chain_json)
        if progress.image_chain_json
        else ImageChainState()
    )

    async def save_image(value: ImageChainState) -> None:
        progress.image_chain_json = value.model_dump_json()
        await _save_progress(user_id, job.id, progress, stage="image")

    try:
        paths = await generate_character_images(
            prompt,
            user_id=user_id,
            storage_directory=directory,
            reference_image=portrait,
            secondary_reference_image=outfit,
            environment_reference_image=await asyncio.to_thread(read_valid_image_data_uri, snapshot.scene.path)
            if not editing
            else None,
            identity_reference=portrait,
            identity_reference_mode=(
                "primary" if editing or snapshot.identity_reference_role == "fullbody" else "portrait_and_outfit"
            ),
            identity_text="" if editing else render_character_identity(snapshot.identity),
            image_edit=editing,
            goal_review="required",
            size=f"{progress.target_width}x{progress.target_height}",
            aspect_ratio=progress.aspect_ratio,
            target_width=progress.target_width,
            target_height=progress.target_height,
            nearest_resolution=True,
            size_enforced=True,
            state=state,
            save_progress=save_image,
            before_submit=before_submit,
            task=AiTaskKey.visuals,
            selection=progress.image_selection,
        )
    except ImageGenerationError as exc:
        if exc.result_unknown:
            raise
        if isinstance(exc, ImageReviewUnavailableError):
            raise VisualCreationError(str(exc), "review_unavailable") from exc
        if state.stop_reason == "goal_review_invalid":
            raise VisualCreationError(str(exc), "review_invalid") from exc
        reviewed = [candidate for candidate in state.candidates if candidate.evaluated and candidate.score is not None]
        if not reviewed:
            raise
        identity_safe = max(
            (candidate for candidate in reviewed if candidate.score >= MEDIA_IDENTITY_ACCEPT_SCORE),
            key=lambda candidate: candidate.score,
            default=None,
        )
        goal = state.goal_reviews.get(identity_safe.path) if identity_safe else None
        begin_opening_repair(
            job,
            progress,
            reason=goal.reason if goal else "候选画面未通过身份或活动要求检查",
            reference_path=opening_repair_reference(progress, state, identity_safe.path if identity_safe else None),
        )
        await _save_progress(user_id, job.id, progress, stage="repairing", repair_count=job.repair_count)
        await _prepare_opening(user_id, job, snapshot, progress, directory)
        return
    resolved = resolve_asset_reference(paths[0])
    if resolved is None:
        raise VisualStateError("起始画面无法读取")
    generated_data, _width, _height = await asyncio.to_thread(
        _frame_bytes,
        await asyncio.to_thread(resolved[0].read_bytes),
        progress.aspect_ratio,
    )
    normalized, _width, _height = await _prepare_geometry(progress, generated_data)
    progress.pose_path = await save_companion_asset_async(
        normalized,
        user_id=user_id,
        label="visual_pose",
        ext="png",
        directory=directory,
    )
    progress.image_confirmed = False
    progress.motion_prompt = ""
    progress.image_origin = state.selected_origin()
    candidate = state.best()
    goal = state.goal_reviews.get(candidate.path) if candidate else None
    with Image.open(resolved[0]) as original_image:
        original_size = ImageOps.exif_transpose(original_image).size
    if goal is not None and goal.verdict == "pass" and original_size == (progress.frame_width, progress.frame_height):
        progress.opening_review = OpeningReview(
            image_hash=await asyncio.to_thread(opening_image_hash, progress.pose_path),
            requirements_hash=opening_requirements_hash(job, progress),
            score=candidate.score,
            verdict=goal.verdict,
            reason=goal.reason,
        )
    await _save_progress(user_id, job.id, progress, stage="image")


async def _prepare_opening(
    user_id: int,
    job: VisualJob,
    snapshot: ComboSnapshot,
    progress: VisualJobProgress,
    directory: str,
) -> None:
    async def before_submit() -> None:
        await _require_active_job(user_id, job.id, progress.generation_id)

    if progress.creation_plan is None:
        await _save_progress(user_id, job.id, progress, stage="prepare")
        await ensure_creation_plan(user_id, job, snapshot, progress, before_submit=before_submit)
        await _save_progress(user_id, job.id, progress, stage="prepare")
    if progress.repair_reason and not progress.repair_description:
        await _save_progress(user_id, job.id, progress, stage="repairing")
        await ensure_opening_repair(user_id, job, progress, snapshot=snapshot, before_submit=before_submit)
        await _save_progress(user_id, job.id, progress, stage="repairing")
    await _prepare_pose(user_id, job, snapshot, progress, directory)
    await _save_progress(user_id, job.id, progress, stage="checking")
    review = await review_existing_opening(user_id, job, progress, snapshot=snapshot, before_submit=before_submit)
    await _save_progress(user_id, job.id, progress, stage="checking")
    if opening_passed(review):
        return
    if review.status != "complete":
        raise VisualCreationError(
            "画面检查暂未完成，可以稍后继续" if review.status == "unavailable" else "画面检查未完成，可以重新准备画面",
            "review_unavailable" if review.status == "unavailable" else "review_invalid",
        )
    begin_opening_repair(
        job,
        progress,
        reason=review.reason,
        reference_path=opening_repair_reference(
            progress,
            ImageChainState.model_validate_json(progress.image_chain_json) if progress.image_chain_json else None,
            progress.pose_path if review.score is not None and review.score >= MEDIA_IDENTITY_ACCEPT_SCORE else None,
        ),
    )
    await _save_progress(user_id, job.id, progress, stage="repairing", repair_count=job.repair_count)
    await _prepare_opening(user_id, job, snapshot, progress, directory)


async def _check_confirmed_geometry(progress: VisualJobProgress) -> None:
    resolved = resolve_asset_reference(progress.pose_path)
    if resolved is None:
        raise VisualStateError("已确认的起始画面无法读取")
    data = await asyncio.to_thread(resolved[0].read_bytes)
    original_size = await asyncio.to_thread(probe_visual_image, data)
    if not visual_aspect_matches(*original_size, progress.target_width, progress.target_height):
        raise VisualStateError("起始画面与目标画幅不一致，请重新准备画面")
    progress.frame_width, progress.frame_height = original_size
    progress.aspect_ratio = _delivery_aspect(progress)


async def _prepare_motion(
    user_id: int,
    job: VisualJob,
    snapshot: ComboSnapshot,
    progress: VisualJobProgress,
) -> None:
    if progress.motion_prompt:
        return
    await _save_progress(user_id, job.id, progress, stage="motion")
    pose = await asyncio.to_thread(read_valid_image_data_uri, progress.pose_path)
    if not pose:
        raise VisualStateError("已确认的起始画面无法读取")

    async def before_submit() -> None:
        await _require_active_job(user_id, job.id, progress.generation_id)

    script = await vision_chat_json(
        user_id,
        VISUAL_MOTION_DESIGN,
        {
            "persona": snapshot.persona,
            "action": progress.action_description,
            "creation_plan": progress.creation_plan.model_dump() if progress.creation_plan else None,
            "kind": snapshot.action.clip_kind,
            "duration_seconds": progress.duration_seconds,
            "feedback": job.feedback,
            "video_feedback": job.video_feedback,
            "customization_priority": progress.customization_priority,
            "use_when": snapshot.action.use_when,
            "avoid_when": snapshot.action.avoid_when,
        },
        response_model=VisualMotionScript,
        schema_name="visual_motion_script",
        preserve_choices=("feasible", "failure_kind"),
        reference_images=(pose,),
        before_submit=before_submit,
        task=AiTaskKey.visuals,
    )
    if not script.feasible or not script.motion_prompt.strip():
        raise VisualMotionError(script)
    progress.motion_prompt = script.motion_prompt
    progress.visual_description = script.visual_description
    progress.outfit_description = script.outfit_description
    await _save_progress(user_id, job.id, progress, stage="video")


def _opening_asset(job: VisualJob, progress: VisualJobProgress) -> VisualAsset:
    return VisualAsset(
        media_kind="image",
        media_path=progress.pose_path,
        width=progress.frame_width or progress.target_width,
        height=progress.frame_height or progress.target_height,
        image_origin=progress.image_origin,
        visual_description=progress.visual_description,
        outfit_description=progress.outfit_description or "",
        creation_plan=progress.creation_plan,
        feedback=job.feedback,
        video_feedback=job.video_feedback,
    )


async def _process_candidate(
    user_id: int,
    job: VisualJob,
    progress: VisualJobProgress,
    directory: str,
) -> VisualAsset:
    resolved = resolve_asset_reference(progress.source_path)
    if resolved is None:
        raise VisualStateError("已下载的视频无法读取")
    source = resolved[0]
    temporary = source.with_name(f"visual-normalized-{progress.generation_id}.mp4")
    try:
        result = await run_blocking(prepare_desktop_video, source, temporary, aspect_ratio=_delivery_aspect(progress))
        video = await save_companion_asset_async(
            await asyncio.to_thread(temporary.read_bytes),
            user_id=user_id,
            label="visual_video",
            ext="mp4",
            directory=directory,
        )
        poster = await save_companion_asset_async(
            result.cover,
            user_id=user_id,
            label="visual_cover",
            ext="webp",
            directory=directory,
        )
        image_path = progress.pose_path
        if result.fitted:
            image_path = await save_companion_asset_async(
                result.first_frame,
                user_id=user_id,
                label="visual_pose",
                ext="png",
                directory=directory,
            )
        progress.frame_width, progress.frame_height = result.width, result.height
        chain = MediaChainState.model_validate_json(progress.video_chain_json or "{}")
        video_origin = (
            chain.providers[chain.active_index].generation_origin(ServiceType.video_gen)
            if chain.active_index is not None
            else None
        )
        return VisualAsset(
            media_kind="video",
            media_path=video or "",
            poster_path=poster,
            input_image_path=image_path or "",
            width=result.width,
            height=result.height,
            duration_ms=result.duration_ms,
            image_origin=progress.image_origin,
            video_origin=video_origin,
            visual_description=progress.visual_description,
            outfit_description=progress.outfit_description or "",
            creation_plan=progress.creation_plan,
            feedback=job.feedback,
            video_feedback=job.video_feedback,
        )
    finally:
        await asyncio.to_thread(temporary.unlink, missing_ok=True)


async def _evaluate_candidate(user_id: int, job: VisualJob, progress: VisualJobProgress) -> None:
    candidate = progress.candidate
    if candidate is None:
        raise VisualStateError("制作的视频缺失")
    await _require_active_job(user_id, job.id, progress.generation_id)
    resolved = resolve_asset_reference(candidate.media_path)
    reference = await asyncio.to_thread(read_valid_image_data_uri, progress.pose_path)
    if resolved is None or not reference:
        raise VisualStateError("视频或已确认的参考画面无法读取")
    samples = await asyncio.to_thread(sample_desktop_video_frames, resolved[0])
    uris = tuple(build_data_uri(frame, "image/webp") for frame in samples.frames)
    references = (reference,)
    async with SESSION_LOCAL() as db:
        language = resolve_language(await get_user_setting(db, user_id, "language"))

    async def before_submit() -> None:
        await _require_active_job(user_id, job.id, progress.generation_id)

    if progress.identity_score is None:
        progress.identity_score = await score_character_frames_with_references(
            user_id,
            references,
            uris,
            VISUAL_VIDEO_SCORE.format(accept_score=MEDIA_IDENTITY_ACCEPT_SCORE),
            task=AiTaskKey.visuals,
            before_submit=before_submit,
        )
        await _save_progress(user_id, job.id, progress, stage="review")
        if progress.identity_score is None:
            raise VisualCreationError("视频身份检查暂未完成，可以稍后继续", "review_unavailable")
    if progress.review_verdict is None:
        try:
            review_payload: dict[str, object] = {
                "video_feedback": job.video_feedback,
                "kind": visual_snapshot(job).action.clip_kind,
                "duration_seconds": progress.duration_seconds,
                "frame_times_seconds": samples.times_seconds,
                "reference_frame_count": len(references),
                "output_language": language,
                "action": progress.action_description,
                "creation_plan": progress.creation_plan.model_dump() if progress.creation_plan else None,
                "motion_prompt": progress.motion_prompt,
                "feedback": job.feedback,
                "customization_priority": progress.customization_priority,
            }
            review = await vision_chat_json(
                user_id,
                VISUAL_VIDEO_REVIEW,
                review_payload,
                response_model=VisualVideoReview,
                schema_name="visual_video_review",
                preserve_choices=("verdict",),
                reference_images=(*references, *uris),
                before_submit=before_submit,
                task=AiTaskKey.visuals,
            )
            progress.review_verdict, progress.review_reason = review.verdict, review.reason
        except LlmCallBlockedError:
            raise
        except Exception as exc:
            logger.warning("Visual video review unavailable", extra={"job_id": job.id}, exc_info=True)
            raise VisualCreationError("视频检查暂未完成，可以稍后继续", "review_unavailable") from exc
    await _save_progress(user_id, job.id, progress, stage="review")


async def _describe_frame(user_id: int, job_id: int, progress: VisualJobProgress) -> None:
    if progress.visual_description and progress.outfit_description is not None:
        return
    async with SESSION_LOCAL() as db:
        if not await resolve_vision_chain(db, user_id, task=AiTaskKey.visuals):
            return
    image_path = (
        progress.candidate.input_image_path
        if progress.candidate is not None and progress.candidate.input_image_path
        else progress.pose_path
    )
    reference = await asyncio.to_thread(read_valid_image_data_uri, image_path)
    if not reference:
        return

    async def before_submit() -> None:
        await _require_active_job(user_id, job_id, progress.generation_id)

    try:
        description = await vision_chat_json(
            user_id,
            VISUAL_VIDEO_FRAME_DESCRIPTION,
            {},
            response_model=VisualVideoFrameDescription,
            schema_name="visual_video_frame_description",
            reference_images=(reference,),
            before_submit=before_submit,
            task=AiTaskKey.visuals,
        )
        progress.visual_description = description.description
        progress.outfit_description = description.outfit_description
        if progress.candidate is not None:
            progress.candidate.visual_description = description.description
            progress.candidate.outfit_description = description.outfit_description
    except LlmCallBlockedError:
        return
    except Exception:
        logger.warning("Visual frame description unavailable", extra={"job_id": job_id}, exc_info=True)


def _publication_snapshot(snapshot: ComboSnapshot, progress: VisualJobProgress) -> ComboSnapshot:
    return snapshot.model_copy(update={"plan": progress.creation_plan or snapshot.plan})


async def _finish_job(
    user_id: int,
    job_id: int,
    progress: VisualJobProgress,
    *,
    allow_followup_work: bool = True,
) -> None:
    published: PublishedVisual | None = None
    async with SESSION_LOCAL() as db:
        job, presentation = await publication_rows(db, user_id, job_id)
        saved = visual_progress(job)
        if saved is None or saved.generation_id != progress.generation_id or job.state not in {"queued", "running"}:
            return
        if progress.candidate is None:
            raise VisualStateError("当前制作还没有可用成品")
        passed = (
            progress.review_verdict == "pass"
            and progress.identity_score is not None
            and progress.identity_score >= MEDIA_IDENTITY_ACCEPT_SCORE
        )
        if not passed:
            raise VisualCreationError(progress.review_reason or "成品未通过检查，请重新生成", "quality_failed")
        snapshot = _publication_snapshot(visual_snapshot(job), progress)
        asset = progress.candidate
        checkpoint = progress.model_dump(mode="json")
        publication = await publish_visual_result(db, user_id, job, asset, snapshot=snapshot, checkpoint=checkpoint)
        published = publication.visual
        completed = publication.completed
        if completed is None:
            raise VisualStateError("制作结果未完成提交")
        persisted = {
            "state": "succeeded",
            "stage": "complete",
            "error": None,
            "checkpoint": checkpoint,
            "revision": completed.revision,
            "finished_at": completed.finished_at,
            "updated_at": completed.updated_at,
            "result_visual_id": published.id,
            "result_version_id": published.current_version_id,
            "result_image_id": publication.image_id,
            "result_image_version_id": publication.image_version_id,
        }
        for key, value in persisted.items():
            set_committed_value(job, key, value)
        await settle_visual_steps(
            db,
            user_id,
            job,
            result_ref={
                "visual_id": published.id,
                "visual_version_id": published.current_version_id,
                "version": published.version,
            },
            additional_events=[
                LifeEventInput(
                    event_key=f"visual:{job.id}:{published.id}:{published.version}",
                    kind="visual.ready",
                    source="visuals",
                    fact={"visual_id": published.id, "media_kind": published.media_kind, "version": published.version},
                    result_ref={
                        "visual_id": published.id,
                        "visual_version_id": published.current_version_id,
                        "version": published.version,
                    },
                )
            ],
        )
        if job.purpose == "regenerate":
            state = presentation
            if (
                state is not None
                and state.current_visual_id == published.id
                and not state.pending_command_json
                and get_presentation_snapshot(user_id) is not None
            ):
                await dispatch_play_command(db, user_id, published, source="restore")
        await notify_visuals_updated(db, user_id)
        await db.commit()
    if published is not None and allow_followup_work and not published.title.strip():
        schedule_visual_title(user_id, published.id)
    if published is not None and allow_followup_work and job.purpose == "create":
        notify_visual_ready(user_id)


async def _pause_visual_job(
    user_id: int,
    job_id: int,
    generation_id: str,
    *,
    latest_progress: VisualJobProgress | None = None,
    by_user: bool = False,
) -> None:
    async with SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        progress = visual_progress(job)
        if progress is None or progress.generation_id != generation_id or job.state not in {"queued", "running"}:
            return
        progress = latest_progress or progress
        progress.paused_by_user = by_user
        progress.submission_unknown = visual_progress_has_unknown_submission(progress)
        progress.failure_reason = "result_unknown" if progress.submission_unknown else "temporary"
        job.checkpoint = progress.model_dump(mode="json")
        job.state, job.stage, job.error = "paused", "paused", None
        await notify_visuals_updated(db, user_id)
        await db.commit()


def _visual_job_error(exc: Exception, progress: VisualJobProgress, *, stage: str) -> str:
    if progress.submission_unknown:
        return _unknown_submission_message(progress)
    if isinstance(exc, (VisualStateError, VideoProcessError, ImageGenerationError, VideoQueueStoppedError)):
        return str(exc)
    phase = {
        "prepare": "安排活动",
        "repairing": "完善画面",
        "checking": "检查画面",
        "image": "制作画面",
        "motion": "设计视频动作",
        "video": "制作视频",
        "download": "下载视频",
        "process": "处理视频",
        "review": "检查视频",
    }.get(stage, "准备素材")
    if isinstance(exc, VisualReasoningError):
        return f"{phase}未完成：{exc}"
    return f"{phase}时发生异常，已保留当前进度，可点击“再试一次”继续。"


async def _run_visual_job(user_id: int, job_id: int, *, allow_model_steps: bool = True) -> None:
    generation_id = ""
    directory = ""
    progress: VisualJobProgress | None = None
    try:
        async with SESSION_LOCAL() as db:
            row = await visual_job_row(db, user_id, job_id)
            initial = visual_progress(row)
            if initial is None or initial.paused_by_user or row.state not in {"queued", "running"}:
                return
            generation_id = initial.generation_id
        job_inputs = await _job_inputs(user_id, job_id, generation_id)
        job, snapshot, progress = job_inputs
        clip_kind = snapshot.action.clip_kind
        progress.aspect_ratio = _delivery_aspect(progress)
        if visual_progress_has_unknown_submission(progress):
            progress.submission_unknown = True
            await _save_progress(user_id, job_id, progress, stage=job.stage)
            raise VisualStateError(_unknown_submission_message(progress))
        directory = f"visuals/{job_id}/{generation_id}"
        # 已有评估结论时只收尾，不因维护暂停丢弃本次检查。
        if not allow_model_steps and progress.candidate is not None and progress.review_verdict is not None:
            await _finish_job(user_id, job_id, progress, allow_followup_work=allow_model_steps)
            return
        if not allow_model_steps and not (  # 刻意不含 candidate：待评估候选不算在途视频工作
            progress.source_path
            or _queued_video_request(progress)
            or MediaChainState.model_validate_json(progress.video_chain_json or "{}").result_url
        ):
            raise GenerationWorkPaused
        if not visual_progress_has_video_work(progress):
            await _prepare_opening(user_id, job, snapshot, progress, directory)
            progress.image_confirmed = True
            await _save_progress(user_id, job_id, progress, stage="video")
            if job.target_kind == "image":
                progress.candidate = _opening_asset(job, progress)
                progress.review_verdict = "pass"
                progress.identity_score = progress.opening_review.score if progress.opening_review else None
                # 图片成品同样提供桌面与动态读取的画面描述；描述不可用时不阻断发布。
                if allow_model_steps:
                    await _describe_frame(user_id, job_id, progress)
                await _save_progress(user_id, job_id, progress, stage="review")
                await _finish_job(user_id, job_id, progress, allow_followup_work=allow_model_steps)
                return
            async with SESSION_LOCAL() as db:
                current_job = await visual_job_row(db, user_id, job_id, lock=True)
                saved = visual_progress(current_job)
                if (
                    current_job.state not in {"queued", "running"}
                    or saved is None
                    or saved.generation_id != progress.generation_id
                ):
                    return
                opening = await publish_opening_result(
                    db,
                    user_id,
                    current_job,
                    _opening_asset(job, progress).model_copy(update={"input_image_path": progress.pose_path}),
                    snapshot=_publication_snapshot(snapshot, progress),
                )
                set_committed_value(current_job, "result_image_id", opening.id)
                set_committed_value(current_job, "result_image_version_id", opening.current_version_id)
                current_job.updated_at = utc_now()
                await notify_visuals_updated(db, user_id)
                await db.commit()
        chain = MediaChainState.model_validate_json(progress.video_chain_json or "{}")
        if not chain.providers:
            seconds, providers = await resolve_visual_video_plan(
                user_id,
                clip_kind=clip_kind,
                duration_seconds=progress.duration_seconds,
                aspect_ratio=progress.aspect_ratio,
                target_width=progress.target_width,
                target_height=progress.target_height,
                selection=progress.video_selection,
            )
            progress.duration_seconds = seconds
            chain = MediaChainState(providers=providers, generation_id=progress.generation_id)
            progress.video_chain_json = chain.model_dump_json()
            await _save_progress(user_id, job_id, progress, stage="video")
        if not visual_progress_has_video_work(progress):
            await _check_confirmed_geometry(progress)
        while not progress.motion_prompt and not visual_progress_has_video_work(progress):
            try:
                await _prepare_motion(user_id, job, snapshot, progress)
            except VisualMotionError as exc:
                if exc.script.failure_kind == "duration":
                    minimum = exc.script.minimum_duration_seconds
                    if progress.motion_replan_count or minimum is None or minimum > 15:
                        raise VisualCreationError(str(exc), "activity_unsupported") from exc
                    progress.motion_replan_count = 1
                    await _save_progress(user_id, job_id, progress, stage="motion")
                    try:
                        seconds, providers = await resolve_visual_video_plan(
                            user_id,
                            clip_kind=clip_kind,
                            duration_seconds=minimum,
                            aspect_ratio=progress.aspect_ratio,
                            target_width=progress.target_width,
                            target_height=progress.target_height,
                            selection=progress.video_selection,
                        )
                    except VisualStateError as planning_error:
                        raise VisualCreationError(str(planning_error), "activity_unsupported") from planning_error
                    if seconds < minimum or seconds == progress.duration_seconds:
                        raise VisualCreationError(
                            "当前视频时长不足以完成这个活动，请选择其他动作",
                            "activity_unsupported",
                        ) from exc
                    progress.duration_seconds = seconds
                    chain = MediaChainState(providers=providers, generation_id=progress.generation_id)
                    progress.video_chain_json = chain.model_dump_json()
                    await _save_progress(user_id, job_id, progress, stage="video")
                    continue
                if exc.script.failure_kind == "unsupported":
                    raise
                begin_opening_repair(
                    job,
                    progress,
                    reason=str(exc),
                    reference_path=progress.pose_path,
                )
                await _save_progress(user_id, job_id, progress, stage="repairing", repair_count=job.repair_count)
                await _prepare_opening(user_id, job, snapshot, progress, directory)
                progress.image_confirmed = True
                await _save_progress(user_id, job_id, progress, stage="video")
                continue
        if progress.submission_unknown:
            raise VisualStateError(video_failure_message("result_unknown"))
        while True:
            if progress.candidate is not None:
                if not allow_model_steps:
                    raise GenerationWorkPaused
                if progress.outfit_description is None:
                    await _describe_frame(user_id, job_id, progress)
                await _evaluate_candidate(user_id, job, progress)
                result = VisualCandidateResult(
                    asset=progress.candidate,
                    verdict=progress.review_verdict or "review",
                    reason=progress.review_reason,
                )
                candidate = MediaCandidate(
                    path=progress.candidate.media_path,
                    attempt=chain.active_index or 0,
                    evaluated=True,
                    accepted=progress.review_verdict == "pass",
                    result_json=result.model_dump_json(),
                )
                chain.accept_score(candidate, progress.identity_score)
                if not any(item.path == candidate.path for item in chain.candidates):
                    chain.candidates.append(candidate)
                progress.candidates_json = json.dumps(
                    [
                        VisualCandidateResult.model_validate_json(entry.result_json).model_dump(mode="json")
                        for entry in chain.candidates
                        if entry.result_json
                    ],
                    ensure_ascii=False,
                )
                if chain.needs_next():
                    progress.candidate = None
                    progress.identity_score = None
                    progress.review_verdict = None
                    progress.review_reason = ""
                    chain.result_url = None
                    progress.source_path = None
                    chain.active_index = None
                    chain.phase = "ready"
                    chain.queue_request_id, chain.video_queue = None, None
                    progress.video_chain_json = chain.model_dump_json()
                    await _save_progress(user_id, job_id, progress, stage="video")
                    continue
                best = chain.best_qualified() or chain.best()
                if best is not None and best.result_json:
                    chosen = VisualCandidateResult.model_validate_json(best.result_json)
                    progress.candidate, progress.identity_score = chosen.asset, best.score
                    progress.review_verdict, progress.review_reason = chosen.verdict, chosen.reason
                break
            if progress.source_path:
                await _save_progress(user_id, job_id, progress, stage="process")
                progress.candidate = await _process_candidate(user_id, job, progress, directory)
                await _save_progress(user_id, job_id, progress, stage="review")
                continue
            if chain.result_url:
                data = await download_media_result(chain.result_url, max_bytes=256 * 1024 * 1024)
                progress.source_path = await save_video_job_asset_async(
                    data,
                    user_id=user_id,
                    job_id=job_id,
                    attempt=chain.active_index if chain.active_index is not None else chain.next_index,
                    generation_id=generation_id,
                    directory=directory,
                )
                chain.result_url = None
                await _save_progress(user_id, job_id, progress, stage="process")
                continue
            if chain.phase == "submitting" and not chain.queue_request_id:
                progress.submission_unknown = True
                await _save_progress(user_id, job_id, progress, stage="video")
                raise VisualStateError(video_failure_message("result_unknown"))
            index = chain.active_index if chain.active_index is not None else chain.next_index
            if index is None or index >= len(chain.providers):
                raise VisualStateError(video_failure_message("provider_failed"))
            owner = VideoQueueOwner(
                kind="visuals",
                user_id=user_id,
                job_id=job_id,
                generation_id=chain.generation_id,
            )
            if not chain.queue_request_id:
                if not allow_model_steps:
                    raise GenerationWorkPaused
                await _require_active_job(user_id, job_id, generation_id)
                if not progress.image_confirmed:
                    raise VisualStateError("起始画面尚未通过检查，请继续制作")
                config = await resolve_frozen_media_provider(user_id, "video_gen", chain.providers[index])
                if config is None:
                    raise VisualStateError(video_failure_message("provider_unavailable"))
                provider = build_provider(config, VideoGenProvider)
                desired_ratio = _provider_ratio(provider, progress.aspect_ratio)
                if desired_ratio is None:
                    # 配置恢复后能力声明可能改变；仍不为回退改写确认过的画面。
                    chain.next_index = index + 1
                    chain.active_index = None
                    chain.phase = "ready"
                    progress.video_chain_json = chain.model_dump_json()
                    await _save_progress(user_id, job_id, progress, stage="video")
                    if chain.next_index >= len(chain.providers):
                        raise VisualStateError(f"当前视频模型均不支持已确认的 {progress.aspect_ratio} 画幅")
                    continue
                frozen_ratio = chain.providers[index].video_aspect_ratio
                if frozen_ratio is None:
                    raise VisualStateError("视频制作任务缺少冻结画幅，请重新制作")
                if not provider.supports_adaptive_aspect_ratio and frozen_ratio not in provider.aspect_ratios:
                    raise VisualStateError("视频模型的冻结画幅已不再可用，请重新选择模型")
                desired_ratio = frozen_ratio
                resolved = resolve_asset_reference(progress.pose_path)
                if resolved is None:
                    raise VisualStateError("已确认的起始画面无法读取")
                raw_frame = await asyncio.to_thread(resolved[0].read_bytes)
                normalized, _width, _height = await asyncio.to_thread(
                    _frame_bytes,
                    raw_frame,
                    None,
                    provider.first_frame_size_range,
                )
                pose = build_data_uri(normalized, "image/png")
                request = VideoGenRequest(
                    prompt=VISUAL_VIDEO_MOTION_TEMPLATE.format(
                        duration_seconds=progress.duration_seconds,
                        cycle=VISUAL_VIDEO_LOOP if clip_kind == "loop" else VISUAL_VIDEO_ONCE,
                        motion=progress.motion_prompt,
                        feedback=job.feedback.strip() or "无",
                        video_feedback=job.video_feedback.strip() or "无",
                        customization_priority=progress.customization_priority,
                        aspect_ratio=desired_ratio,
                    ),
                    duration=progress.duration_seconds,
                    resolution=chain.providers[index].video_resolution,
                    first_frame_image=pose,
                    last_frame_image=pose if clip_kind == "loop" else None,
                    aspect_ratio=None if provider.supports_adaptive_aspect_ratio else desired_ratio,
                )
                await _require_active_job(user_id, job_id, generation_id)
                chain.begin(index)
                chain.phase = "ready"
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, job_id, progress, stage="video")
                chain.queue_request_id = await enqueue_video_attempt(owner, chain.providers[index], index, request)
                chain.phase = "submitting"
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, job_id, progress, stage="video")

            async def on_submitted(_task_id: str) -> None:
                chain.phase = "processing"
                chain.video_queue = None
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, job_id, progress, stage="video")

            async def on_queue(info: VideoQueueInfo | None) -> None:
                chain.video_queue = info
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, job_id, progress, stage="video")

            try:
                status = await wait_video_attempt(
                    chain.queue_request_id,
                    on_submitted=on_submitted,
                    on_queue=on_queue,
                )
            except GenerationWorkPaused:
                raise
            except Exception as exc:
                reason, can_fallback = media_failure_reason(exc)
                if reason == "queue_stopped":
                    stopped_request = chain.queue_request_id
                    chain.phase, chain.active_index, chain.stop_reason = "complete", None, reason
                    chain.queue_request_id, chain.video_queue = None, None
                    progress.submission_unknown = False
                    progress.failure_reason = reason
                    progress.video_chain_json = chain.model_dump_json()
                    await _save_progress(user_id, job_id, progress, stage="video")
                    if stopped_request:
                        await acknowledge_video_attempt(stopped_request)
                if not can_fallback:
                    raise
                failed_request = chain.queue_request_id
                chain.phase, chain.active_index = "ready", None
                chain.queue_request_id, chain.video_queue = None, None
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, job_id, progress, stage="video")
                await acknowledge_video_attempt(failed_request)
                if chain.next_index >= len(chain.providers):
                    raise VisualStateError(video_failure_message(reason)) from exc
                continue
            if status.status == "failed":
                failed_request = chain.queue_request_id
                chain.phase = "ready"
                chain.active_index = None
                chain.queue_request_id, chain.video_queue = None, None
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, job_id, progress, stage="video")
                await acknowledge_video_attempt(failed_request)
                if chain.next_index >= len(chain.providers):
                    raise VisualStateError(video_failure_message(video_provider_failure_reason(status.error)))
                continue
            if not status.download_url:
                raise VisualStateError(video_failure_message("download_failed"))
            chain.result_url = status.download_url
            chain.phase = "storing"
            chain.video_queue = None
            progress.video_chain_json = chain.model_dump_json()
            await _save_progress(user_id, job_id, progress, stage="download")
            await acknowledge_video_attempt(chain.queue_request_id)
        chain.finish()
        progress.video_chain_json = chain.model_dump_json()
        await _finish_job(user_id, job_id, progress, allow_followup_work=allow_model_steps)
    except GenerationWorkPaused:
        if generation_id:
            await _pause_visual_job(user_id, job_id, generation_id)
        logger.info("Visual generation paused before new model calls", extra={"job_id": job_id})
    except asyncio.CancelledError as exc:
        if exc.args == ("paused",) and progress is not None:
            await _pause_visual_job(
                user_id,
                job_id,
                generation_id,
                latest_progress=progress,
                by_user=True,
            )
            request_id = _queued_video_request(progress)
            if request_id:
                await pause_video_attempt(request_id)
        if exc.args == ("discarded",) and progress is not None:
            try:
                request_id = _queued_video_request(progress)
                if request_id:
                    await cancel_video_attempt(request_id)
            except Exception:
                logger.warning("Visual video cancellation unavailable", extra={"job_id": job_id}, exc_info=True)
        raise
    except Exception as exc:
        logger.warning("Visual job failed", extra={"job_id": job_id}, exc_info=True)
        async with SESSION_LOCAL() as db:
            row = await visual_job_row(db, user_id, job_id, lock=True)
            saved = visual_progress(row)
            if saved is not None and saved.generation_id == generation_id and row.state in {"queued", "running"}:
                if isinstance(exc, VisualCreationError) and exc.repair_reason:
                    saved.repair_reason = exc.repair_reason
                saved.submission_unknown = visual_progress_has_unknown_submission(saved) or (
                    isinstance(exc, ImageGenerationError) and exc.result_unknown
                )
                saved.failure_reason = (
                    exc.failure_reason
                    if isinstance(exc, VisualCreationError)
                    else "queue_stopped"
                    if isinstance(exc, VideoQueueStoppedError)
                    else "result_unknown"
                    if saved.submission_unknown
                    else "material_rejected"
                    if isinstance(exc, VideoProcessError) and not isinstance(exc, VideoToolUnavailableError)
                    else "temporary"
                    if visual_progress_has_video_work(saved)
                    else "failed"
                )
                row.checkpoint = saved.model_dump(mode="json")
                row.state = "unknown" if saved.submission_unknown else "failed"
                row.error = _visual_job_error(exc, saved, stage=row.stage)
                await settle_visual_steps(db, user_id, row)
                await notify_visuals_updated(db, user_id)
                await db.commit()
                if allow_model_steps and row.result_image_id is not None:
                    notify_visual_ready(user_id)
    finally:
        if directory:
            try:
                async with SESSION_LOCAL() as db:
                    exists = await db.scalar(
                        select(VisualJob.id).where(VisualJob.id == job_id, VisualJob.user_id == user_id),
                    )
                if exists is None:
                    cleanup_asset_directories_after_task(user_id, [directory])
            except Exception:
                logger.warning("Removed visual asset cleanup deferred", extra={"job_id": job_id}, exc_info=True)


def schedule_visual_job(user_id: int, job_id: int, *, allow_model_steps: bool = True) -> None:
    previous = _JOBS.get(job_id)
    if previous is not None and not previous.done() and not previous.cancelling():
        _NEXT_JOBS[job_id] = (user_id, allow_model_steps)
        return
    if previous is not None and not previous.done():
        _FINISHING_JOBS.setdefault(job_id, set()).add(previous)
    task = asyncio.create_task(
        _run_visual_job(user_id, job_id, allow_model_steps=allow_model_steps),
        name=f"visual-job-{job_id}",
    )
    _JOBS[job_id] = task
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def finished(done: asyncio.Task[None]) -> None:
        finishing = _FINISHING_JOBS.get(job_id)
        if finishing is not None:
            finishing.discard(done)
            if not finishing:
                _FINISHING_JOBS.pop(job_id, None)
        if _JOBS.get(job_id) is done:
            _JOBS.pop(job_id, None)
            queued = _NEXT_JOBS.pop(job_id, None)
            if queued is not None:
                schedule_visual_job(queued[0], job_id, allow_model_steps=queued[1])
        if not done.cancelled() and done.exception() is not None:
            logger.error("Visual task finalization failed", exc_info=done.exception())

    task.add_done_callback(finished)


def cancel_visual_job(job_id: int) -> None:
    _NEXT_JOBS.pop(job_id, None)
    task = _JOBS.get(job_id)
    if task is not None:
        cancel_tasks((task,), message="discarded")


async def pause_visual_job(job_id: int) -> None:
    _NEXT_JOBS.pop(job_id, None)
    await cancel_and_wait((_JOBS.get(job_id),), message="paused")


async def resume_visual_jobs(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        query = select(VisualJob).where(VisualJob.state.in_(("queued", "running")))
        if user_id is not None:
            query = query.where(VisualJob.user_id == user_id)
        rows = list(await db.scalars(query))
    for row in rows:
        progress = visual_progress(row)
        if progress is not None and progress.paused_by_user:
            continue
        safe_resume = (
            progress is not None
            and not visual_progress_has_unknown_submission(progress)
            and visual_progress_has_video_work(progress)
        )
        if safe_resume:
            schedule_visual_job(row.user_id, row.id, allow_model_steps=False)
        elif progress is not None and visual_progress_can_resume(progress):
            if progress.candidate is not None and progress.review_verdict is not None:
                schedule_visual_job(row.user_id, row.id, allow_model_steps=False)
            else:
                await _pause_visual_job(row.user_id, row.id, progress.generation_id)
        else:
            async with SESSION_LOCAL() as db:
                current = await visual_job_row(db, row.user_id, row.id, lock=True)
                saved = visual_progress(current)
                if current.state not in {"queued", "running"} or (saved.generation_id if saved else None) != (
                    progress.generation_id if progress else None
                ):
                    continue
                progress = saved
                current.state = (
                    "unknown" if progress is not None and visual_progress_has_unknown_submission(progress) else "failed"
                )
                if progress is not None:
                    progress.failure_reason = (
                        "result_unknown"
                        if visual_progress_has_unknown_submission(progress)
                        else "temporary"
                        if visual_progress_can_resume(progress)
                        else "failed"
                    )
                    current.checkpoint = progress.model_dump(mode="json")
                current.error = (
                    "制作已中断，请手动继续"
                    if not progress or not progress.submission_unknown
                    else _unknown_submission_message(progress)
                )
                await settle_visual_steps(db, row.user_id, current)
                await notify_visuals_updated(db, row.user_id)
                await db.commit()


async def drain() -> None:
    _NEXT_JOBS.clear()
    tasks = set(_JOBS.values()).union(*_FINISHING_JOBS.values())
    await cancel_and_wait(tasks)
