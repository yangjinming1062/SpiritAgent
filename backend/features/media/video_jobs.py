"""聊天与动态共用的视频任务：提交、轮询、下载与身份检查；凭句柄或已落盘候选恢复。"""

import asyncio
from collections.abc import Collection, Sequence
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from infrastructure.ai.error_classifier import LlmCallBlockedError, LLMRuntimeError
from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.ai.providers.base import ProviderConfig, VideoGenProvider, VideoGenRequest
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.assets.asset_store import (
    build_data_uri,
    dated_asset_directory,
    resolve_asset_reference,
    video_job_asset_path,
)
from infrastructure.assets.image_references import freeze_image_references, materialize_image_references
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.assets.references import asset_paths
from infrastructure.assets.write_protection import asset_write_context
from infrastructure.async_io import KeyedLocks
from infrastructure.database import SESSION_LOCAL
from infrastructure.domain_locks import lock_domain
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from infrastructure.video_processing.desktop import face_motion_comparison, sample_desktop_video_frames
from infrastructure.video_processing.face_detail import face_sequence_sheet
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video
from infrastructure.video_processing.motion_review import video_motion_profile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.configuration.settings.timezone import resolve_user_timezone
from features.jobs.service import bind_job_assets
from features.media.chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
    video_failure_message,
    video_provider_failure_reason,
)
from features.media.continuations import wait_for_media
from features.media.contracts import VideoJobSnapshot, video_delivery_error
from features.media.delivery import deliver_video_result
from features.media.identity_contracts import CharacterReferenceRole, VisualIdentitySnapshot
from features.media.identity_review import review_character_frames_with_references, review_media_quality
from features.media.image_jobs import cleanup_user_image_jobs
from features.media.models import VideoGenJob
from features.media.prompts import (
    CHARACTER_MEDIA_VIDEO_REVIEW,
    REFERENCED_VIDEO_MOTION,
    character_reference_roles,
    personal_performance_goal,
)
from features.media.queries import chat_video_by_media_id, reject_video_delivery, video_job_snapshot_async
from features.media.reference_reader import visual_identity_is_current
from features.media.review_contracts import (
    MediaSelection,
    ObservedVideoHold,
    ReviewDecision,
    ReviewEvidence,
    VisualReview,
    merge_review_evidence,
    needs_review,
)
from features.media.review_evidence import asset_file_hash, read_review_evidence, review_media_intact
from features.media.review_views import MediaJobView, MediaSlotView
from features.media.selection import CandidateReviewBlocked, select_delivery, select_observed_hold
from features.media.video_detail_delivery import (
    FacePerformanceRejected,
    freeze_face_refinement,
    store_performance_video,
)
from features.media.video_queue.contracts import VideoQueueInfo, VideoQueueOwner, VideoQueueStoppedError
from features.media.video_queue.service import (
    acknowledge_video_attempt,
    cancel_owner_video_attempts,
    enqueue_video_attempt,
    find_video_attempt,
    get_video_attempt_status,
)
from features.media.video_results import receive_video_attempt

logger = get_logger(__name__)

_POLL_TASKS = TaskScope("media.video_jobs")
_RESULT_LOCKS = KeyedLocks[int]()
_TERMINAL_STATES = ("succeeded", "failed", "unknown", "cancelled")


class _VideoJobParams(BaseModel):
    """任务冻结的请求参数与出镜身份。"""

    model_config = ConfigDict(extra="forbid")

    task: AiTaskKey
    request_scope: str | None = None
    duration: int
    resolution: str
    reference_images: tuple[Annotated[str, Field(min_length=1)], ...]
    reference_guidance: str = ""
    performance_context: str = ""
    portrait_reference: str | None = None
    portrait_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    compose_activity_opening: bool = Field(default=False, strict=True)
    require_face_review: bool = False
    aspect_ratio: str | None
    identity_reference_path: str | None
    identity_snapshot: VisualIdentitySnapshot | None
    identity_reference_role: CharacterReferenceRole
    goal: str
    asset_generation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    asset_directory: str

    @model_validator(mode="after")
    def validate_identity(self) -> "_VideoJobParams":
        has_identity = self.identity_snapshot is not None
        if self.portrait_reference and not has_identity:
            raise ValueError("面容依据必须属于此次冻结的出镜身份")
        if has_identity != bool(self.identity_reference_path) or (has_identity and not self.reference_images):
            raise ValueError("出镜视频须同时冻结身份版本、身份来源和送检参考图")
        if self.compose_activity_opening and not (
            has_identity and self.identity_reference_role == "portrait" and len(self.reference_images) == 2
        ):
            raise ValueError("按活动分步绘制开场需要头像与承担身体职责的当前造型图")
        return self

    @property
    def required_dimensions(self) -> tuple[str, ...]:
        if self.require_face_review and self.identity_snapshot is not None:
            return ("identity", "goal", "quality", "opening", "face")
        return (
            ("identity", "goal", "quality", "opening")
            if self.identity_snapshot is not None
            else ("goal", "quality", "opening")
        )

    def generation_request(
        self,
        prompt: str,
        *,
        seed: int | None = None,
        opening_frame_instruction: str | None = None,
        recompose_opening: bool = False,
        fixed_ambient_scene: bool = False,
        observed_hold_images: tuple[str, str] | None = None,
    ) -> VideoGenRequest:
        opening = personal_performance_goal(opening_frame_instruction or prompt, self.performance_context)
        prompt = personal_performance_goal(prompt, self.performance_context)
        motion = REFERENCED_VIDEO_MOTION.format(prompt=prompt) if self.reference_guidance else None
        opening_prompt = (
            f"{opening}\n{self.reference_guidance}" if self.reference_guidance else opening_frame_instruction
        )
        return VideoGenRequest(
            prompt=f"{self.reference_guidance}\n\n{motion}" if motion else prompt,
            first_frame_motion=motion,
            opening_frame_prompt=opening_prompt,
            pose_goal=opening if self.identity_snapshot is not None else None,
            compose_activity_opening=self.compose_activity_opening and not opening_frame_instruction,
            fixed_ambient_scene=fixed_ambient_scene and not self.reference_images,
            observed_hold_images=observed_hold_images,
            opening_composition_goal=opening
            if opening_frame_instruction
            and recompose_opening
            and self.identity_reference_role == "portrait"
            and len(self.reference_images) == 2
            else None,
            noise_seed=seed,
            duration=self.duration,
            resolution=self.resolution,
            reference_images=self.reference_images,
            aspect_ratio=self.aspect_ratio,
        )


def _abandoned(job: VideoGenJob) -> bool:
    """已登记清理的任务不再推进状态。"""
    return job.cleanup_requested_at is not None


def _settled(job: VideoGenJob) -> bool:
    """已登记清理或已到终态的任务不再接受状态覆写。"""
    return _abandoned(job) or job.state in _TERMINAL_STATES


class _JobSettledError(LlmCallBlockedError):
    """轮询期间任务行已删除或已由其他路径终结。"""


async def _require_live_job(job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _settled(row):
            raise _JobSettledError


async def _require_video_review_call(job_id: int, user_id: int) -> None:
    await _require_live_job(job_id)
    require_new_generation_call(user_id)


async def _require_not_abandoned(job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _abandoned(row):
            raise _JobSettledError


def _stored_asset_file(user_id: int, storage_path: str | None) -> Path | None:
    """本用户裸资产路径对应的已存在文件。"""
    parsed = parse_companion_asset_path(storage_path)
    if parsed is None or parsed[0] != user_id:
        return None
    resolved = asset_store.resolve_companion_asset_path(*parsed)
    return resolved[0] if resolved is not None else None


def _on_video_task_error(task: asyncio.Task) -> None:
    """视频后台任务失败落日志；poll 路径自身已记录详细原因,这里只兜底未捕获异常。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception("video background task failed", exc_info=exc)


async def drain() -> None:
    """取消并等待所有后台视频任务完成，吞下 CancelledError。"""
    await _POLL_TASKS.drain()


async def _sync_changed_job_assets(db: AsyncSession) -> None:
    for row in tuple(db.new | db.dirty):
        if not isinstance(row, VideoGenJob) or row.id is None:
            continue
        paths = asset_paths([row.params_json, row.checkpoint, row.video_url, row.candidate_video_url], row.user_id)
        present = {
            path for path in paths if (resolved := resolve_asset_reference(path)) is not None and resolved[0].is_file()
        }
        if present:
            await bind_job_assets(db, row.user_id, row.id, present, "dependency")


async def _update_job(job_id: int, **fields: object) -> None:
    """用全新短会话更新任务行——后台任务比请求会话长寿，绝不复用调用方的 ``db``；行在读写之间被 GC（管理员 DELETE 等）则提前返回。终态保护：succeeded/failed 行不再被覆写，避免晚到的 poll 失败翻转已成功状态。"""

    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id, with_for_update=True)
        if job is None:
            return
        if _settled(job) or job.state == "paused":
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await _sync_changed_job_assets(db)
        await db.commit()


async def _get_job(db: AsyncSession, job_id: int, user_id: int) -> VideoGenJob | None:
    """按 user_id 过滤：task_id 来自模型的工具参数，只能读到本用户的任务；会话归属由调用方核对。"""

    stmt = select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _compatible_video_configs(
    chain: list[ProviderConfig],
    *,
    reference_images: bool,
    reference_image_count: int | None = None,
    duration: int,
    resolution: str,
    include_upscaled: bool = True,
) -> list[ProviderConfig]:
    count = reference_image_count if reference_image_count is not None else int(reference_images)
    return [
        config
        for config in chain
        if _supports_video_output(
            build_provider(config, VideoGenProvider), duration, resolution, count, include_upscaled=include_upscaled
        )
    ]


def _supports_video_output(
    provider: VideoGenProvider, duration: int, resolution: str, reference_count: int, *, include_upscaled: bool = False
) -> bool:
    if reference_count and not provider.supports_reference_image_count(reference_count):
        return False
    native = provider.native_resolution(resolution)
    available = provider.available_resolutions(
        duration=duration, reference_images=bool(reference_count), include_upscaled=include_upscaled
    )
    return bool(native and native.upper() in {item.upper() for item in available})


async def video_duration_options(
    db: AsyncSession,
    user_id: int,
    *,
    task: AiTaskKey,
    preferred: Sequence[str],
    self_reference_count: int,
) -> dict[str, list[int]]:
    """按同一供应商的原生时长、参考模式和可交付分辨率列举计划选项。"""
    chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    providers = [build_provider(config, VideoGenProvider) for config in chain]
    return {
        mode: sorted(
            {
                seconds
                for provider in providers
                for seconds in provider.durations or ()
                if any(_supports_video_output(provider, seconds, resolution, count) for resolution in preferred)
            }
        )
        for mode, count in (("plain", 0), ("self", self_reference_count))
    }


async def ensure_video_capability(
    user_id: int,
    *,
    task: AiTaskKey,
    reference_images: bool,
    reference_image_count: int | None = None,
    duration: int,
    resolution: str,
    allowed_durations: Collection[int] | None = None,
    allowed_resolutions: Collection[str] | None = None,
) -> None:
    """创建视频任务前核对参考模式、时长与分辨率，并在调用方允许的范围内给出可用档位。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    if not chain:
        raise MissingLlmConfigError("视频生成服务未配置")
    if _compatible_video_configs(
        chain,
        reference_images=reference_images,
        reference_image_count=reference_image_count,
        duration=duration,
        resolution=resolution,
    ):
        return
    providers = [build_provider(config, VideoGenProvider) for config in chain]
    count = reference_image_count if reference_image_count is not None else int(reference_images)
    candidates = [p for p in providers if (p.supports_reference_image_count(count) if count else True)]
    if not candidates:
        raise MissingLlmConfigError("已配置的视频供应商都不支持角色参考生视频")
    hints = []
    if not any(p.durations is None or duration in p.durations for p in candidates):
        options = sorted({d for p in candidates for d in p.durations or () if d in (allowed_durations or (d,))})
        hints.append("可选时长（秒）：" + "、".join(map(str, options)))
    options = sorted(
        {
            value.upper()
            for provider in candidates
            for value in provider.resolutions or ()
            if value.upper() in (allowed_resolutions or (value.upper(),))
            and _compatible_video_configs(
                chain,
                reference_images=reference_images,
                reference_image_count=reference_image_count,
                duration=duration,
                resolution=value,
            )
        }
    )
    if options and resolution.upper() not in options:
        hints.append("可选分辨率：" + "、".join(options))
    detail = "；".join(hints) or "同一供应商不同时支持所选时长与分辨率，请调整其一"
    raise MissingLlmConfigError(f"已配置的视频供应商不支持本次的时长或分辨率（{detail}）")


async def select_video_resolution(
    user_id: int,
    *,
    task: AiTaskKey,
    reference_images: bool,
    reference_image_count: int | None = None,
    duration: int,
    preferred: Sequence[str],
) -> str | None:
    """按偏好顺序返回供应商链里有能接单的第一档分辨率，大小写写法沿用首个可接单供应商的声明；没有则返回 None，由调用方在付费前处理。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    for resolution in preferred:
        compatible = _compatible_video_configs(
            chain,
            reference_images=reference_images,
            reference_image_count=reference_image_count,
            duration=duration,
            resolution=resolution,
            include_upscaled=False,
        )
        if compatible:
            declared = build_provider(compatible[0], VideoGenProvider).resolutions or ()
            return next((value for value in declared if value.lower() == resolution.lower()), resolution)
    return None


async def prepare_video_job(
    db: AsyncSession,
    *,
    user_id: int,
    task: AiTaskKey,
    session_id: str | None,
    prompt: str,
    goal: str | None = None,
    source_message_id: int | None = None,
    request_scope: str | None = None,
    asset_directory: str | None = None,
    duration: int,
    resolution: str,
    aspect_ratio: str | None,
    reference_images: tuple[str, ...] = (),
    reference_guidance: str = "",
    performance_context: str = "",
    portrait_reference: str | None = None,
    identity_reference_path: str | None = None,
    identity: VisualIdentitySnapshot | None = None,
    identity_reference_role: CharacterReferenceRole = "portrait",
    media_id: str | None = None,
    include_upscaled: bool = True,
) -> VideoJobSnapshot:
    """冻结能力与请求，在调用方事务内创建任务；提交供应商之前必须提交事务。"""
    chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    compatible = _compatible_video_configs(
        chain,
        reference_images=bool(reference_images),
        reference_image_count=len(reference_images),
        duration=duration,
        resolution=resolution,
        include_upscaled=include_upscaled,
    )
    if not compatible:
        raise MissingLlmConfigError("未配置支持本次参考输入、时长和分辨率的视频供应商")
    state = MediaChainState(
        providers=[FrozenMediaProvider.from_config(config) for config in compatible],
        refine_character_faces=identity is not None,
    )
    if not asset_directory:
        # 空串同缺省处理：按任务创建日冻结，不能把空目录写进任务参数落成根目录平铺。
        asset_directory = dated_asset_directory(utc_now(), await resolve_user_timezone(db, user_id) or "UTC")
    params = _VideoJobParams(
        goal=goal or prompt,
        task=task,
        request_scope=request_scope,
        duration=duration,
        asset_directory=asset_directory,
        resolution=resolution,
        reference_images=reference_images,
        reference_guidance=reference_guidance,
        performance_context=performance_context,
        portrait_reference=portrait_reference,
        require_face_review=identity is not None,
        compose_activity_opening=identity is not None
        and identity_reference_role == "portrait"
        and len(reference_images) == 2,
        aspect_ratio=aspect_ratio,
        identity_reference_path=identity_reference_path,
        identity_snapshot=identity,
        identity_reference_role=identity_reference_role,
        asset_generation_id=state.generation_id,
    )
    frozen_references = await freeze_image_references(
        {
            **{f"ref{index}": value for index, value in enumerate(params.reference_images)},
            "portrait": params.portrait_reference,
        },
        user_id=user_id,
        generation_id=state.generation_id,
        directory=asset_directory,
    )
    params.portrait_reference = frozen_references.pop("portrait", None)
    params.reference_images = tuple(frozen_references.values())
    if identity:
        params.portrait_reference = params.portrait_reference or params.reference_images[0]
        params.portrait_hash = await asyncio.to_thread(asset_file_hash, params.portrait_reference)
        if not params.portrait_hash:
            raise ValueError("无法读取此次出镜的面容依据")
    job = VideoGenJob(
        user_id=user_id,
        conversation_id=int(session_id) if session_id is not None else None,
        source_message_id=source_message_id,
        structured_reply=session_id is not None,
        media_id=media_id,
        prompt=prompt,
        params_json=params.model_dump(mode="json"),
        state="queued",
        stage="retry_pending",
        checkpoint=state.model_dump(mode="json"),
    )
    db.add(job)
    await db.flush()
    if job.conversation_id is not None:
        await wait_for_media(
            db,
            user_id=job.user_id,
            owner_kind="chat.video",
            owner_job_id=job.id,
            dependency_job_id=job.id,
            operation_id=f"chat-video:{job.source_key}",
            automatic=True,
            context={},
        )
    await bind_job_assets(db, user_id, job.id, asset_paths(params.model_dump(mode="json"), user_id), "input")
    return await video_job_snapshot_async(db, job)


async def submit_video_job(user_id: int, job_id: int) -> VideoJobSnapshot:
    """提交已落库的新任务；打断后按原状态收尾，未知结果禁止自动重发。"""
    async with SESSION_LOCAL() as db:
        if await _get_job(db, job_id, user_id) is None:
            raise ValueError("Video task is no longer available")
    try:
        submitted = await _submit_next_video(job_id, user_id)
    except BaseException:
        _spawn_poll(job_id, user_id)
        raise
    if submitted:
        _spawn_poll(job_id, user_id)
    async with SESSION_LOCAL() as db:
        job = await _get_job(db, job_id, user_id)
        if job is None:
            raise ValueError("Video task is no longer available")
        return await video_job_snapshot_async(db, job)


async def enqueue_video_job(
    db: AsyncSession,
    *,
    user_id: int,
    task: AiTaskKey,
    session_id: str,
    prompt: str,
    goal: str | None = None,
    source_message_id: int | None = None,
    request_scope: str,
    asset_directory: str | None = None,
    duration: int,
    resolution: str,
    aspect_ratio: str | None,
    reference_images: tuple[str, ...] = (),
    reference_guidance: str = "",
    performance_context: str = "",
    portrait_reference: str | None = None,
    identity_reference_path: str | None = None,
    identity: VisualIdentitySnapshot | None = None,
    identity_reference_role: CharacterReferenceRole = "portrait",
    media_id: str,
) -> tuple[VideoJobSnapshot, bool]:
    """聊天请求按账户与稳定的媒体身份受理；重放只读取原任务。"""
    await lock_domain(db, user_id, f"chat.video:{media_id}")
    existing = await chat_video_by_media_id(db, user_id, session_id, request_scope, media_id)
    if existing is not None:
        return existing, False
    job = await prepare_video_job(
        db,
        user_id=user_id,
        task=task,
        session_id=session_id,
        prompt=prompt,
        goal=goal,
        source_message_id=source_message_id,
        request_scope=request_scope,
        asset_directory=asset_directory,
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images,
        reference_guidance=reference_guidance,
        performance_context=performance_context,
        portrait_reference=portrait_reference,
        identity_reference_path=identity_reference_path,
        identity=identity,
        identity_reference_role=identity_reference_role,
        media_id=media_id,
    )
    await _sync_changed_job_assets(db)
    await db.commit()
    return await submit_video_job(user_id, job.id), True


def _spawn_poll(job_id: int, user_id: int) -> None:
    _POLL_TASKS.spawn(
        job_id,
        lambda: _poll_and_finalize(job_id),
        user_id=user_id,
        cancel_on_maintenance=False,
        on_error=_on_video_task_error,
    )


async def _record_failure(job_id: int, *, reason: str, settle_unknown: bool = False) -> None:
    """保存真实失败或未知提交，已登记候选随任务检查点保留。"""
    logger.warning("video job failure", extra={"job_id": job_id, "reason": reason})
    user_msg = video_failure_message(reason)
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _abandoned(row):
            return
        if row.state in _TERMINAL_STATES and not (settle_unknown and row.state == "unknown"):
            return
        if reason == "result_unknown":
            row.state, row.stage, row.error_reason, row.error = "unknown", "result_unknown", reason, user_msg
        else:
            reject_video_delivery(row, reason)
        await _sync_changed_job_assets(db)
        await db.flush()
        await deliver_video_result(db, await video_job_snapshot_async(db, row))
        emit_ws_event(db, user_id=row.user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
        await db.commit()


async def _finalize_best_video(job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _settled(row):
            return
        state = MediaChainState.model_validate(row.checkpoint or {})
        params = _VideoJobParams.model_validate(row.params_json)
        user_id = row.user_id
    try:
        choice = state.selections.get(0)
        if choice is None or choice.mode == "best_available":
            choice = await select_delivery(
                state.candidates,
                user_id=user_id,
                task=params.task,
                goal=params.goal,
                required=params.required_dimensions,
                before_submit=partial(_require_video_review_call, job_id, user_id),
            )
    except CandidateReviewBlocked:
        await _record_failure(job_id, reason="content_policy_blocked")
        return
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _settled(row):
            return
        state = MediaChainState.model_validate(row.checkpoint or {})
        state.selections[0] = choice
        best = state.delivery_candidate(0, params.required_dimensions)
        current = await user_is_active(db, user_id) and (
            not params.identity_snapshot or await visual_identity_is_current(db, user_id, params.identity_snapshot)
        )
        if current and best and await asyncio.to_thread(review_media_intact, best.review.evidence, best.path):
            row.review_result_json = best.review.model_dump(mode="json")
            row.video_url, row.state, row.stage = best.path, "succeeded", "complete"
            row.error_reason = row.error = None
            row.selection_json = choice.model_dump(mode="json")
            state.finish()
            row.checkpoint = state.model_dump(mode="json")
            row.candidate_video_url = None
        else:
            reason = "quality_failed"
            if not current:
                reason = "identity_changed"
            elif any(
                not candidate.excluded
                and (review := candidate.review)
                and review.status in {"unavailable", "invalid"}
                and review.model_verdict != "fail"
                for candidate in state.candidates
            ):
                reason = "quality_unavailable"
            reject_video_delivery(
                row,
                reason,
                choice if reason == "quality_failed" and choice.mode == "not_delivered" else None,
            )
        await _sync_changed_job_assets(db)
        await db.flush()
        await deliver_video_result(db, await video_job_snapshot_async(db, row))
        emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
        await db.commit()


def _plan_video_correction(state: MediaChainState, entry: MediaCandidate, hold: ObservedVideoHold | None) -> bool:
    if entry.attempt != state.active_index:
        return False
    instruction = entry.review.correction_instruction
    opening_instruction = entry.review.opening_frame_instruction
    if entry.review.opening and entry.review.opening.verdict != "pass":
        opening_instruction = entry.review.opening.correction_instruction
        instruction = (f"从这幅画面开始：{opening_instruction}。之后接续原要求的动作。{instruction}")[:400]
    if entry.review.motion:
        instruction = ("按原目标完成规定步数，双脚停止向前交替并保持落脚位置。" + instruction)[:400]
    if not instruction and entry.review.identity and entry.review.identity.verdict != "pass":
        instruction = "依据原始参考重建同一角色的面部轮廓、物种特征与固定标记，使这些特征随动作和视角连续呈现。"
    if not entry.review.face_check_passes:
        instruction = (
            "保留原定景别与动作，按实际头部角度和视线还原原参考中可见的眼睑、瞳孔、鼻翼和唇形。"
            "面部与耳根、下颌和颈部自然相连，转动时保持同刻的眼睑开合、目光和口形。" + instruction
        )[:400]
    return state.plan_local_correction(
        entry.review.correction_notes(),
        instructions={0: instruction},
        opening_frame_instruction=opening_instruction,
        fixed_ambient_scene=entry.review.fixed_ambient_scene,
        observed_hold=hold,
    )


async def _evaluate_stored_video(job_id: int) -> str:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _settled(row):
            return "invalid"
        path = row.candidate_video_url or row.video_url
        if not path:
            return "invalid"
        params = _VideoJobParams.model_validate(row.params_json)
        user_id, prompt = row.user_id, params.goal
        state = MediaChainState.model_validate(row.checkpoint or {})
        entry = next((c for c in state.candidates if c.path == path), None)
        if entry and entry.review and entry.review.status == "blocked":
            return "blocked"
        if entry is None:
            entry = MediaCandidate(path=path, attempt=state.current_attempt_index())
            state.candidates.append(entry)
        row.checkpoint = state.model_dump(mode="json")
        await db.commit()
    local = _stored_asset_file(user_id, path)
    if local is None:
        return "invalid"
    try:
        probe = await asyncio.to_thread(probe_video, local)
        samples = await asyncio.to_thread(
            sample_desktop_video_frames,
            local,
            sample_count=9,
            include_opening_transition=True,
            include_scene_changes=True,
            include_face_details=entry.result_json is None
            or (params.identity_snapshot is not None and entry.evidence is None),
        )
    except Exception:
        return "invalid"
    uris = tuple(build_data_uri(frame, "image/webp") for frame in samples.frames)

    async def save_candidate() -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None or _settled(current):
                raise _JobSettledError
            latest = MediaChainState.model_validate(current.checkpoint or {})
            latest.candidates = [c for c in latest.candidates if c.path != path] + [entry]
            current.checkpoint = latest.model_dump(mode="json")
            await bind_job_assets(db, user_id, job_id, asset_paths(current.checkpoint, user_id), "review")
            await db.commit()

    async def save_identity(evidence: ReviewEvidence) -> None:
        entry.evidence = evidence
        await save_candidate()

    async def save_quality(evidence: ReviewEvidence) -> None:
        entry.result_json = evidence.model_dump_json()
        if entry.review is None:
            entry.review = VisualReview(required=("goal", "quality"), evidence=evidence, status="unavailable")
        else:
            entry.review.evidence = evidence
            entry.review.status = "unavailable"
        await save_candidate()

    before_submit = partial(_require_video_review_call, job_id, user_id)

    needs_identity = params.identity_snapshot is not None
    reference_paths = params.reference_images
    frozen_references: tuple[str, ...] = ()
    if needs_identity and entry.evidence:
        identity_images = await read_review_evidence(entry.evidence)
        frozen_references = identity_images[: len(reference_paths)]
    elif reference_paths:
        references = await materialize_image_references(
            {str(index): value for index, value in enumerate(reference_paths)}, user_id=user_id
        )
        frozen_references = tuple(references.values())
    identity_decision = entry.review.identity if entry.review else None
    identity_available = True
    face_details = tuple((time, build_data_uri(detail, "image/webp")) for time, detail in samples.face_details)
    if needs_identity and (identity_decision is None or identity_decision.verdict not in {"pass", "fail"}):
        roles = character_reference_roles(params.identity_reference_role, len(frozen_references) > 1)
        identity = await review_character_frames_with_references(
            user_id,
            frozen_references,
            uris,
            CHARACTER_MEDIA_VIDEO_REVIEW.format(roles=roles),
            task=params.task,
            before_submit=before_submit,
            evidence=entry.evidence,
            save_evidence=save_identity,
            media_path=path,
            frame_times_seconds=samples.times_seconds,
            face_details=face_details,
        )
        entry.evidence = identity.evidence
        if entry.review is None:
            entry.review = VisualReview(required=("identity", "goal", "quality"), status="unavailable")
        entry.review.identity = ReviewDecision(verdict=identity.verdict, reason=identity.reason)
        identity_available = identity.status == "complete"
        entry.review.status = "unavailable" if identity_available else identity.status
        await save_candidate()
    if identity_available and needs_review(entry.review):
        old = entry.review
        motion_profile = None
        if entry.result_json is None:
            try:
                motion_profile = await asyncio.to_thread(
                    video_motion_profile,
                    local,
                    probe.duration_seconds,
                    character=needs_identity,
                    full_frames=samples.frames,
                )
            except (VideoProcessError, OSError, ValueError):
                return "invalid"
        face_sheet = None
        face_motion = None
        if params.require_face_review and samples.face_details and frozen_references and entry.result_json is None:
            if params.portrait_reference:
                portrait = _stored_asset_file(user_id, params.portrait_reference)
                if portrait is None or (
                    params.portrait_hash
                    and await asyncio.to_thread(asset_file_hash, params.portrait_reference) != params.portrait_hash
                ):
                    return "invalid"
                original = await asyncio.to_thread(portrait.read_bytes)
            else:
                original, _ = await resolve_reference_bytes(frozen_references[0])
            face_sheet = await asyncio.to_thread(face_sequence_sheet, samples.face_details, original)
        if state.face_motion_source and entry.result_json is None:
            source_file = _stored_asset_file(user_id, state.face_motion_source)
            if not source_file or not face_sheet:
                return "invalid"
            face_motion = await asyncio.to_thread(face_motion_comparison, source_file, samples.face_details)
            if face_motion is None:
                return "invalid"
        quality = await review_media_quality(
            user_id,
            (*frozen_references, *uris),
            reference_count=len(frozen_references),
            generation_prompt=prompt,
            media_path=path,
            frame_times_seconds=samples.times_seconds,
            face_details=face_details,
            face_sequence=(face_sheet[0], build_data_uri(face_sheet[1], "image/webp")) if face_sheet else None,
            face_motion=build_data_uri(face_motion.image, "image/webp") if face_motion else None,
            face_structure_loss_times=face_motion.loss_times if face_motion else (),
            face_transition=build_data_uri(samples.face_transition, "image/webp") if samples.face_transition else None,
            scene_transition=build_data_uri(samples.scene_transition, "image/webp")
            if samples.scene_transition
            else None,
            face_transient_times=samples.face_transient_times,
            motion_profile=motion_profile,
            character=needs_identity and params.require_face_review,
            task=params.task,
            evidence=ReviewEvidence.model_validate_json(entry.result_json) if entry.result_json else None,
            previous=old,
            save_evidence=save_quality,
            before_submit=before_submit,
        )
        quality_evidence = quality.evidence
        entry.result_json = quality_evidence.model_dump_json() if quality_evidence else None
        quality.identity = old.identity if old else None
        entry.review = quality
    else:
        quality_evidence = ReviewEvidence.model_validate_json(entry.result_json) if entry.result_json else None
    if needs_identity:
        entry.review.required = tuple(dict.fromkeys((*params.required_dimensions, *entry.review.required)))
        if quality_evidence is not None:
            entry.review.evidence = merge_review_evidence(entry.evidence, quality_evidence)
    if (
        entry.review.status == "complete"
        and entry.review.evidence is not None
        and all(
            (decision := getattr(entry.review, name)) is not None and decision.verdict in {"pass", "fail"}
            for name in entry.review.required
        )
    ):
        # 结论与依据已归入 VisualReview，不再单独保留阶段证据。
        entry.evidence = entry.result_json = None
    recheck = entry.review.status in {"unavailable", "invalid"} and not entry.review_retried
    if recheck:
        entry.review_retried = True
    entry.evaluated = True
    await save_candidate()
    hold = None
    if entry.attempt == state.active_index and state.can_plan_local_correction():
        try:
            hold = await select_observed_hold(
                entry, goal=prompt, user_id=user_id, task=params.task, before_submit=before_submit
            )
        except CandidateReviewBlocked:
            return "blocked"
    async with SESSION_LOCAL() as db:
        current = await db.get(VideoGenJob, job_id, with_for_update=True)
        if current is None or _settled(current):
            return "invalid"
        state = MediaChainState.model_validate(current.checkpoint or {})
        repair_planned = _plan_video_correction(state, entry, hold)
        if recheck:
            decision = "recheck"
        elif entry.review.status == "blocked":
            state.stop_reason = "content_policy_blocked"
            decision = "blocked"
        elif (
            entry.review.status == "complete"
            and entry.review.model_verdict != "pass"
            and (repair_planned or state.next_index < len(state.providers))
            and not state.receive_only
        ):
            current.state, current.stage = "queued", "retry_pending"
            current.candidate_video_url = None
            state.phase = "ready"
            decision = "retry"
        else:
            decision = "complete"
        current.checkpoint = state.model_dump(mode="json")
        current.review_result_json = entry.review.model_dump(mode="json")
        await db.flush()
        await deliver_video_result(db, await video_job_snapshot_async(db, current))
        emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
        await db.commit()
    return decision


async def video_review_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id))
        if row is None or _abandoned(row):
            raise RequestError(status_code=404, message="找不到视频任务")
        ready = row.state == "succeeded"
        return MediaJobView(
            id=row.id,
            kind=row.kind,
            state=row.state,
            stage=row.stage,
            revision=row.revision,
            required_slots=1,
            ready_slots=int(ready),
            complete=ready,
            slots=[
                MediaSlotView(
                    slot=0,
                    media_id=row.media_id or str(row.id),
                    status="ready" if ready else row.state,
                    url=asset_store.client_asset_url(row.video_url) if ready else None,
                    error=video_delivery_error(row.state),
                )
            ],
        )


def _queue_owner(job: VideoGenJob, state: MediaChainState) -> VideoQueueOwner:
    return VideoQueueOwner(kind="media", user_id=job.user_id, job_id=job.id, generation_id=state.generation_id)


async def inspect_video_queue_owner(
    owner: VideoQueueOwner, before_submit: bool = True, request_id: str | None = None
) -> Literal["ready", "pending", "paused", "gone"]:
    """提交准入通过媒体所有者核对，队列不访问业务 ORM。"""
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, owner.job_id)
        if (
            job is None
            or job.user_id != owner.user_id
            or _abandoned(job)
            or job.state in {"succeeded", "failed", "cancelled"}
        ):
            return "gone"
        if before_submit and job.state == "unknown":
            return "paused"
        state = MediaChainState.model_validate(job.checkpoint or {})
        if state.generation_id != owner.generation_id:
            return "gone"
        if request_id and state.active_queue_request and state.active_queue_request != request_id:
            return "gone"
        params = _VideoJobParams.model_validate(job.params_json)
        if (
            before_submit
            and params.identity_snapshot is not None
            and not await visual_identity_is_current(db, job.user_id, params.identity_snapshot)
        ):
            return "gone"
        if is_user_in_maintenance(job.user_id):
            return "paused"
        if not state.active_queue_request:
            return "pending"
    return "ready"


def _verified_hold_images(state: MediaChainState, hold: ObservedVideoHold) -> tuple[str, str] | None:
    for candidate in state.candidates:
        evidence = (
            candidate.review.evidence
            if candidate.can_correct_pose
            and candidate.review.face_check_passes
            and (candidate.review.goal.verdict == "fail" or candidate.review.motion)
            else None
        )
        if (
            evidence
            and evidence.media_hash == hold.source_sha256
            and hold.opening in evidence.images
            and hold.resting in evidence.images
            and review_media_intact(evidence, candidate.path)
        ):
            return hold.opening.path, hold.resting.path
    return None


async def _submit_next_video(job_id: int, user_id: int) -> bool:
    """只登记共享队列；付费提交和轮询由队列后台任务拥有。"""
    while True:
        if is_user_in_maintenance(user_id):
            return False
        async with SESSION_LOCAL() as db:
            row = await db.get(VideoGenJob, job_id)
            if row is None or _abandoned(row) or row.state != "queued":
                return False
            state = MediaChainState.model_validate(row.checkpoint or {})
            params = _VideoJobParams.model_validate(row.params_json)
            owner = _queue_owner(row, state)
            prompt = row.prompt
        if state.stop_reason or state.next_index >= len(state.providers):
            await _fail_or_keep_best(job_id, state.stop_reason or "provider_unavailable")
            return False
        index = state.next_index
        frozen = state.providers[index]
        request_id = await find_video_attempt(owner, index, frozen)
        config = await resolve_frozen_media_provider(user_id, "video_gen", frozen) if request_id is None else None
        planned_detail = config is not None and await freeze_face_refinement(state, index, config)
        if request_id is None and (config is None or planned_detail):
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or _abandoned(current) or current.state != "queued":
                    return False
                latest = MediaChainState.model_validate(current.checkpoint or {})
                if latest.next_index != index or latest.generation_id != state.generation_id:
                    return False
                if config is None:
                    latest.next_index += 1
                else:
                    latest.face_refinement_by_attempt.setdefault(index, state.face_refinement_by_attempt[index])
                current.checkpoint = latest.model_dump(mode="json")
                await _sync_changed_job_assets(db)
                await db.commit()
            if config is None:
                continue
        if request_id is None:
            correction = state.local_correction if state.is_correction_attempt(index) else None
            opening = correction.opening_frame_instruction if correction else None
            hold_images = (
                await asyncio.to_thread(_verified_hold_images, state, correction.observed_hold)
                if correction and correction.observed_hold
                else None
            )
            request_id = await enqueue_video_attempt(
                owner,
                frozen,
                index,
                params.generation_request(
                    state.generation_prompt(index, prompt),
                    seed=state.local_seed(index) if frozen.provider == "local" else None,
                    opening_frame_instruction=opening,
                    recompose_opening=bool(opening and state.needs_pose_correction(index)),
                    fixed_ambient_scene=bool(correction and correction.fixed_ambient_scene),
                    observed_hold_images=hold_images,
                ),
            )
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None or _abandoned(current) or current.state != "queued":
                return False
            latest = MediaChainState.model_validate(current.checkpoint or {})
            if latest.next_index != index or latest.generation_id != state.generation_id:
                return False
            latest.begin(index)
            # processing 在业务层包含排队；只有队列实际发送才进入其 submitting 状态。
            latest.phase = "processing"
            latest.queue_request_id = request_id
            current.state, current.stage = "running", "processing"
            current.checkpoint = latest.model_dump(mode="json")
            await _sync_changed_job_assets(db)
            await db.commit()
        return True


async def _queue_progress(job_id: int, request_id: str, info: VideoQueueInfo | None) -> None:
    """队列进度只作为 WS 事件转发；权威状态在供应商尝试行，不落业务检查点。"""
    async with SESSION_LOCAL() as db:
        current = await db.get(VideoGenJob, job_id)
        if current is None or _settled(current):
            return
        state = MediaChainState.model_validate(current.checkpoint or {})
        if state.active_queue_request != request_id:
            return
        emit_ws_event(
            db,
            user_id=current.user_id,
            event_type="video_gen.queue",
            payload={
                "task_id": str(job_id),
                "session_id": str(current.conversation_id) if current.conversation_id is not None else None,
                "media_id": current.media_id,
                "video_queue": info.model_dump(mode="json") if info else None,
            },
        )
        await db.commit()


async def _fail_or_keep_best(job_id: int, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _settled(row):
            return
        state = MediaChainState.model_validate(row.checkpoint or {})
    if state.candidates and reason in {"provider_failed", "provider_unavailable", "quality_failed"}:
        await _finalize_best_video(job_id)
    else:
        await _record_failure(job_id, reason=reason)


async def _poll_and_finalize(job_id: int) -> None:
    """后台主循环：供应商任务和本地候选均可恢复；仅未知提交禁止自动重发。"""
    with asset_write_context(inherit=False):
        delay = 1
        retrying = False
        while True:
            try:
                await _poll_and_finalize_locked(job_id)
            except SQLAlchemyError:
                if not retrying:
                    logger.warning("video job database operation awaits retry", extra={"job_id": job_id}, exc_info=True)
                retrying = True
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                if retrying:
                    logger.info("video job database operation resumed", extra={"job_id": job_id})
                return


async def _poll_and_finalize_locked(job_id: int, *, reattach: int = 0) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
        if job is None:
            return
        # 幂等护栏：上一次已终结的行不再重复下载/发事件；``_update_job`` 内部的终态检查是双保险。
        if _settled(job):
            logger.info("skipping already-finalized job", extra={"job_id": job_id, "status": job.state})
            return
        user_id = job.user_id
        params = _VideoJobParams.model_validate(job.params_json)

    try:
        if job.state == "queued":
            if await _submit_next_video(job_id, user_id):
                return await _poll_and_finalize_locked(job_id)
            return
        state = MediaChainState.model_validate(job.checkpoint or {})
        if (
            (job.state == "running" and job.stage == "evaluating")
            and state.phase == "ready"
            and not job.candidate_video_url
            and job.video_url
        ):
            await _finalize_best_video(job_id)
            return
        if job.state == "running" and job.stage in ("downloading", "evaluating"):
            candidate_path = job.candidate_video_url
            if not candidate_path:
                candidate_path = video_job_asset_path(
                    user_id,
                    job_id,
                    state.current_attempt_index(),
                    generation_id=params.asset_generation_id,
                    directory=params.asset_directory,
                )
                if _stored_asset_file(user_id, candidate_path) is None:
                    candidate_path = None
            if candidate_path:
                await _update_job(job_id, state="running", stage="evaluating", candidate_video_url=candidate_path)
                decision = await _evaluate_stored_video(job_id)
                if decision == "recheck":
                    return await _poll_and_finalize_locked(job_id)
                if decision == "blocked":
                    await _record_failure(job_id, reason="content_policy_blocked")
                    return
                if decision == "complete":
                    await _finalize_best_video(job_id)
                    return
                if decision == "invalid":
                    await _fail_or_keep_best(job_id, "quality_failed")
                    return
                if await _submit_next_video(job_id, user_id):
                    return await _poll_and_finalize_locked(job_id)
                return
        if (job.state == "running" and job.stage == "downloading") and state.result_url:
            try:
                storage_url = await _download_and_store(
                    state.result_url,
                    job=job,
                    chain=state,
                    params=params,
                    allow_new=not is_user_in_maintenance(user_id),
                )
            except (LlmCallBlockedError, SQLAlchemyError):
                raise
            except VideoQueueStoppedError:
                await _record_failure(job_id, reason="queue_stopped")
                return
            except FacePerformanceRejected as exc:
                if exc.can_fallback and state.needs_next():
                    state.phase, state.active_index, state.result_url = "ready", None, None
                    await _update_job(
                        job_id, state="queued", stage="retry_pending", checkpoint=state.model_dump(mode="json")
                    )
                    return await _poll_and_finalize_locked(job_id)
                await _fail_or_keep_best(job_id, exc.reason)
                return
            except Exception:
                logger.warning("known video result awaits download recovery", extra={"job_id": job_id}, exc_info=True)
                if job.video_url:
                    await _fail_or_keep_best(job_id, "download_failed")
                    return
                state.resume_download()
                await _update_job(
                    job_id,
                    checkpoint=state.model_dump(mode="json"),
                    error_reason="download_failed",
                    error=video_failure_message("download_failed"),
                )
                return
            await _update_job(
                job_id,
                state="running",
                stage="evaluating",
                candidate_video_url=storage_url,
                error_reason=None,
                error=None,
            )
            return await _poll_and_finalize_locked(job_id)
        if (job.state == "running" and job.stage == "evaluating") and job.video_url:
            await _finalize_best_video(job_id)
            return

        if not state.queue_request_id:
            # 制作状态必须对应持久队列；缺失引用时保留未知结果，防止重发。
            await _fail_or_keep_best(job_id, "result_unknown")
            return

        async def on_queue(info: VideoQueueInfo | None) -> None:
            await _queue_progress(job_id, state.queue_request_id, info)

        async def save_result(chain: MediaChainState) -> None:
            await _require_not_abandoned(job_id)
            await _update_job(
                job_id,
                state="running" if chain.result_url else "queued",
                stage="downloading" if chain.result_url else "retry_pending",
                checkpoint=chain.model_dump(mode="json"),
            )

        try:
            reason = await receive_video_attempt(state, save_result, on_queue=on_queue)
        except GenerationWorkPaused:
            return
        if state.result_url:
            return await _poll_and_finalize_locked(job_id)
        elif state.stop_reason:
            await _fail_or_keep_best(job_id, reason or state.stop_reason)
        elif await _submit_next_video(job_id, user_id):
            return await _poll_and_finalize_locked(job_id)

    except (_JobSettledError, GenerationWorkPaused):
        return
    except SQLAlchemyError:
        raise
    except Exception:
        logger.exception("unhandled exception in video poll worker", extra={"job_id": job_id})
        try:
            async with SESSION_LOCAL() as db:
                latest = await db.get(VideoGenJob, job_id)
            if latest is not None and latest.state == "queued" and not _abandoned(latest):
                latest_state = MediaChainState.model_validate(latest.checkpoint or {})
                if (index := latest_state.next_index) < len(latest_state.providers) and await find_video_attempt(
                    _queue_owner(latest, latest_state), index, latest_state.providers[index]
                ):
                    logger.warning("registered video attempt awaits reattachment", extra={"job_id": job_id})
                    if reattach < 3:
                        await asyncio.sleep(2)
                        return await _poll_and_finalize_locked(job_id, reattach=reattach + 1)
                    return
            if latest is not None and (latest.state == "running" and latest.stage == "processing"):
                latest_state = MediaChainState.model_validate(latest.checkpoint)
                if latest_state.queue_request_id:
                    logger.warning("queued video awaits recovery", extra={"job_id": job_id})
                    return
            if latest is not None and (latest.state == "running" and latest.stage in ("downloading", "evaluating")):
                latest_state = MediaChainState.model_validate(latest.checkpoint or {})
                latest_params = _VideoJobParams.model_validate(latest.params_json)
                candidate = latest.candidate_video_url or video_job_asset_path(
                    user_id,
                    job_id,
                    latest_state.current_attempt_index(),
                    generation_id=latest_params.asset_generation_id,
                    directory=latest_params.asset_directory,
                )
                if _stored_asset_file(user_id, candidate) is not None:
                    logger.warning("stored video awaits recovery", extra={"job_id": job_id, "path": candidate})
                    return
            await _fail_or_keep_best(job_id, "worker_failed")
        except SQLAlchemyError:
            raise
        except Exception:
            logger.exception("could not update video job after worker error", extra={"job_id": job_id})


async def _download_and_store(
    download_url: str, *, job: VideoGenJob, chain: MediaChainState, params: _VideoJobParams, allow_new: bool
) -> str:
    async def save(value: MediaChainState) -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job.id, with_for_update=True)
            if current is None or _abandoned(current) or current.state != "running":
                raise _JobSettledError
            if MediaChainState.model_validate(current.checkpoint).generation_id != value.generation_id:
                raise _JobSettledError
            current.checkpoint = value.model_dump(mode="json")
            await _sync_changed_job_assets(db)
            await db.commit()

    return await store_performance_video(
        download_url,
        owner=_queue_owner(job, chain),
        chain=chain,
        generation_id=params.asset_generation_id,
        directory=params.asset_directory,
        portrait=(params.portrait_reference or params.reference_images[0]) if params.identity_snapshot else None,
        portrait_hash=params.portrait_hash,
        goal=personal_performance_goal(params.goal, params.performance_context),
        duration=params.duration,
        keep_first=False,
        keep_last=False,
        allow_new=allow_new,
        save=save,
        guard=lambda: _require_live_job(job.id),
        on_queue=partial(_queue_progress, job.id),
    )


async def resume_pending_video_jobs(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        query = select(VideoGenJob.id, VideoGenJob.user_id, VideoGenJob.state).where(
            VideoGenJob.cleanup_requested_at.is_(None), VideoGenJob.state.in_(("queued", "running", "unknown"))
        )
        if user_id is not None:
            query = query.where(VideoGenJob.user_id == user_id)
        jobs = list(await db.execute(query))
    for job_id, owner, state in jobs:
        if state == "unknown":
            try:
                await receive_video_result(owner, job_id)
            except ValueError:
                continue
        else:
            _spawn_poll(job_id, owner)


def _result_lock(job_id: int) -> asyncio.Lock:
    return _RESULT_LOCKS[job_id]


async def cleanup_user_video_jobs(user_id: int) -> None:
    """处理已失去所属历史的作业；外部撤销失败保留任务供下一次补偿，绝不重新生成。"""
    async with SESSION_LOCAL() as db:
        ids = list(
            await db.scalars(
                select(VideoGenJob.id).where(
                    VideoGenJob.user_id == user_id, VideoGenJob.cleanup_requested_at.is_not(None)
                )
            )
        )
    for job_id in ids:
        async with _result_lock(job_id):
            await _POLL_TASKS.cancel_and_wait(job_id)
            async with SESSION_LOCAL() as db:
                row = await db.get(VideoGenJob, job_id)
                if row is None or row.user_id != user_id or row.cleanup_requested_at is None:
                    continue
                params = _VideoJobParams.model_validate(row.params_json)
                state = MediaChainState.model_validate(row.checkpoint or {})
            await cancel_owner_video_attempts("media", job_id, state.generation_id)
            paths = asset_paths([row.video_url, row.candidate_video_url, row.checkpoint], user_id)
            attempts = {state.current_attempt_index(), *(entry.attempt for entry in state.candidates)}
            paths.update(
                video_job_asset_path(
                    user_id, job_id, attempt, generation_id=params.asset_generation_id, directory=params.asset_directory
                )
                for attempt in attempts
            )
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or current.user_id != user_id or current.cleanup_requested_at is None:
                    continue
                await enqueue_asset_cleanup(db, user_id, paths)
                await db.delete(current)
                await _sync_changed_job_assets(db)
                await db.commit()


async def cleanup_user_media(user_id: int) -> None:
    """历史变更提交后的统一收尾：先回收失效视频任务（含远端撤销），再删除无引用文件。"""
    await cleanup_user_video_jobs(user_id)
    await cleanup_user_image_jobs(user_id)
    await cleanup_user_assets(user_id)


async def _resnapshot_video(user_id: int, job_id: int) -> VideoJobSnapshot:
    """短会话重取任务快照；任务行已被删除时按未找到处理。"""
    async with SESSION_LOCAL() as db:
        if (row := await _get_job(db, job_id, user_id)) is None:
            raise ValueError("找不到视频任务")
        return await video_job_snapshot_async(db, row)


async def receive_video_result(user_id: int, job_id: int) -> VideoJobSnapshot:
    """按原句柄接收已付费结果，再由原任务自动检查和交付。"""
    async with _result_lock(job_id):
        async with SESSION_LOCAL() as db:
            row = await _get_job(db, job_id, user_id)
            if row is None:
                raise ValueError("找不到视频任务")
            if _abandoned(row) or row.state not in {"unknown", "running"} or _POLL_TASKS.running(job_id) is not None:
                return await video_job_snapshot_async(db, row)
        params = _VideoJobParams.model_validate(row.params_json)
        state = MediaChainState.model_validate(row.checkpoint or {})
        result_url = state.result_url
        request_id = state.active_queue_request
        if row.state == "unknown" and not request_id and not result_url:
            return await _resnapshot_video(user_id, job_id)
        if request_id:
            try:
                result = await get_video_attempt_status(request_id)
            except (LLMRuntimeError, VideoQueueStoppedError) as exc:
                failure, can_fallback = media_failure_reason(exc)
            else:
                if result is None or result.status not in ("succeeded", "failed"):
                    return await _resnapshot_video(user_id, job_id)
                failure = video_provider_failure_reason(result.error) if result.status == "failed" else None
                can_fallback = failure == "provider_failed"
                if request_id == state.queue_request_id:
                    result_url = result.download_url or result_url
            if failure and not (request_id == state.face_request_id and can_fallback and result_url):
                if failure != "result_unknown":
                    await _record_failure(job_id, reason=failure, settle_unknown=True)
                    await acknowledge_video_attempt(request_id)
                return await _resnapshot_video(user_id, job_id)
        candidate = row.candidate_video_url or video_job_asset_path(
            user_id,
            job_id,
            state.current_attempt_index(),
            generation_id=params.asset_generation_id,
            directory=params.asset_directory,
        )
        local = _stored_asset_file(user_id, candidate)
        if local is None and not result_url:
            return await _resnapshot_video(user_id, job_id)
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None:
                raise ValueError("视频任务已删除")
            if _abandoned(current) or current.state != row.state or current.revision != row.revision:
                return await video_job_snapshot_async(db, current)
            state.result_url = result_url
            state.stop_reason = ""
            state.resume_download()
            if request_id == state.queue_request_id:
                state.queue_request_id = None
            state.phase = "evaluating" if local else "storing"
            current.state, current.stage = "running", "evaluating" if local else "downloading"
            current.candidate_video_url = candidate if local else None
            current.checkpoint = state.model_dump(mode="json")
            current.error = current.error_reason = None
            emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
            await db.flush()
            await deliver_video_result(db, await video_job_snapshot_async(db, current))
            await _sync_changed_job_assets(db)
            await db.commit()
        if request_id and request_id != state.face_request_id:
            await acknowledge_video_attempt(request_id)
        _spawn_poll(job_id, user_id)
        return await _resnapshot_video(user_id, job_id)


async def accept_checked_video(db: AsyncSession, user_id: int, job_id: int) -> bool:
    row = await db.scalar(
        select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id).with_for_update()
    )
    if row is None or _abandoned(row):
        return False
    params = _VideoJobParams.model_validate(row.params_json)
    if params.identity_snapshot and not await visual_identity_is_current(db, user_id, params.identity_snapshot):
        return False
    if not await user_is_active(db, user_id):
        return False
    if row.state != "succeeded" or not row.video_url or not row.selection_json or not row.review_result_json:
        return False
    review = VisualReview.model_validate(row.review_result_json)
    selection = MediaSelection.model_validate(row.selection_json)
    return selection.permits(review, params.required_dimensions) and await asyncio.to_thread(
        review_media_intact, review.evidence, row.video_url
    )
