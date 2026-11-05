"""聊天与动态共用的视频任务：提交、轮询、下载与身份检查；凭句柄或已落盘候选恢复。"""

import asyncio
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Literal
from uuid import uuid4
from weakref import WeakValueDictionary

from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
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
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from infrastructure.video_processing.desktop import sample_desktop_video_frames
from infrastructure.video_processing.ffmpeg import probe_video
from pydantic import BaseModel, ConfigDict, Field
from runtime.background import TaskScope
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.configuration.settings.timezone import resolve_user_timezone
from features.jobs.service import bind_job_assets
from features.media.chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    resolve_frozen_media_provider,
    video_failure_message,
    video_provider_failure_reason,
)
from features.media.continuations import authorize_media_waits, wait_for_media
from features.media.contracts import VideoJobSnapshot
from features.media.delivery import deliver_video_result
from features.media.identity_contracts import CharacterReferenceRole, VisualIdentitySnapshot
from features.media.identity_review import review_character_frames_with_references, review_media_quality
from features.media.image_jobs import cleanup_user_image_jobs
from features.media.manual_reviews import ManualAction, approve_candidate, manual_candidate_id, manual_candidate_views
from features.media.models import VideoGenJob
from features.media.prompts import CHARACTER_MEDIA_SELF_VIDEO_REVIEW, CHARACTER_MEDIA_VIDEO_REVIEW
from features.media.queries import video_job_snapshot_async
from features.media.reference_reader import visual_identity_is_current
from features.media.review_contracts import (
    ReviewDecision,
    ReviewEvidence,
    VisualReview,
    candidate_review_key,
    merge_review_evidence,
    needs_review,
    review_basis,
    review_passes,
)
from features.media.review_evidence import read_review_evidence, review_media_intact
from features.media.review_views import (
    MediaJobView,
    MediaManualRequest,
    MediaReviewRequest,
    MediaSlotView,
    review_request_matches,
)
from features.media.video_queue.contracts import VideoQueueInfo, VideoQueueOwner
from features.media.video_queue.service import (
    acknowledge_video_attempt,
    cancel_owner_video_attempts,
    enqueue_video_attempt,
    find_video_attempt,
    get_video_attempt_status,
)
from features.media.video_results import download_and_store_video, receive_video_attempt

logger = get_logger(__name__)

_POLL_TASKS = TaskScope("media.video_jobs")
_RESULT_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
_TERMINAL_STATES = ("succeeded", "failed", "unknown", "cancelled")
_RESOLUTION_RANK = {"480P": 0, "720P": 1, "768P": 1, "1080P": 2, "2K": 3}


class _VideoJobParams(BaseModel):
    """任务冻结的请求参数与出镜身份。"""

    model_config = ConfigDict(extra="forbid")

    task: AiTaskKey
    request_scope: str | None = None
    duration: int
    resolution: str
    reference_images: tuple[str, ...] = ()
    aspect_ratio: str | None
    identity_reference_path: str | None
    identity_snapshot: VisualIdentitySnapshot | None
    identity_reference_role: CharacterReferenceRole = "portrait"
    asset_generation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    asset_directory: str


def _abandoned(job: VideoGenJob) -> bool:
    """已登记清理的任务不再推进状态。"""
    return job.cleanup_requested_at is not None


def _settled(job: VideoGenJob) -> bool:
    """已登记清理或已到终态的任务不再接受状态覆写。"""
    return _abandoned(job) or job.state in _TERMINAL_STATES


class _JobSettledError(Exception):
    """轮询期间任务行已删除或已由其他路径终结。"""


async def _require_live_job(job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _settled(row):
            raise _JobSettledError


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
    chain: list[ProviderConfig], *, reference_images: bool, duration: int, resolution: str
) -> list[ProviderConfig]:
    compatible = []
    for config in chain:
        provider = build_provider(config, VideoGenProvider)
        if reference_images and not provider.supports_reference_images:
            continue
        if reference_images:
            maximum = provider.max_resolution(duration=duration, reference_images=True)
            native = provider.native_resolution(resolution)
            maximum_rank = _RESOLUTION_RANK.get((maximum or "").upper(), -1)
            native_rank = _RESOLUTION_RANK.get((native or "").upper(), -1)
            if native_rank < 0 or native_rank > maximum_rank:
                continue
        if provider.durations is not None and duration not in provider.durations:
            continue
        if provider.resolutions is not None and resolution.lower() not in {
            value.lower() for value in provider.resolutions
        }:
            continue
        compatible.append(config)
    return compatible


async def ensure_video_capability(
    user_id: int,
    *,
    task: AiTaskKey,
    reference_images: bool,
    duration: int,
    resolution: str,
    allowed_durations: Collection[int] | None = None,
    allowed_resolutions: Collection[str] | None = None,
) -> None:
    """占用视频名额前核对参考模式、时长与分辨率，并在调用方允许的范围内给出可用档位。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    if not chain:
        raise MissingLlmConfigError("视频生成服务未配置")
    if _compatible_video_configs(chain, reference_images=reference_images, duration=duration, resolution=resolution):
        return
    providers = [build_provider(config, VideoGenProvider) for config in chain]
    candidates = [p for p in providers if p.supports_reference_images or not reference_images]
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
            and _compatible_video_configs(chain, reference_images=reference_images, duration=duration, resolution=value)
        }
    )
    if options and resolution.upper() not in options:
        hints.append("可选分辨率：" + "、".join(options))
    detail = "；".join(hints) or "同一供应商不同时支持所选时长与分辨率，请调整其一"
    raise MissingLlmConfigError(f"已配置的视频供应商不支持本次的时长或分辨率（{detail}）")


async def select_video_resolution(
    user_id: int, *, task: AiTaskKey, reference_images: bool, duration: int, preferred: Sequence[str]
) -> str | None:
    """按偏好顺序返回供应商链里有能接单的第一档分辨率，大小写写法沿用首个可接单供应商的声明；没有则返回 None，由调用方在付费前处理。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    for resolution in preferred:
        compatible = _compatible_video_configs(
            chain, reference_images=reference_images, duration=duration, resolution=resolution
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
    source_message_id: int | None = None,
    request_scope: str | None = None,
    asset_directory: str | None = None,
    duration: int,
    resolution: str,
    aspect_ratio: str | None,
    reference_images: tuple[str, ...] = (),
    identity_reference_path: str | None = None,
    identity: VisualIdentitySnapshot | None = None,
    identity_reference_role: CharacterReferenceRole = "portrait",
    structured_reply: bool = False,
    media_id: str | None = None,
) -> VideoJobSnapshot:
    """冻结能力与请求，在调用方事务内创建任务；提交供应商之前必须提交事务。"""
    if identity is not None and (not identity_reference_path or not reference_images):
        raise ValueError("Self video requires an identity reference and snapshot")
    chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    compatible = _compatible_video_configs(
        chain, reference_images=bool(reference_images), duration=duration, resolution=resolution
    )
    if not compatible:
        raise MissingLlmConfigError("未配置支持本次参考输入、时长和分辨率的视频供应商")
    state = MediaChainState(providers=[FrozenMediaProvider.from_config(config) for config in compatible])
    if not asset_directory:
        # 空串同缺省处理：按任务创建日冻结，不能把空目录写进任务参数落成根目录平铺。
        asset_directory = dated_asset_directory(utc_now(), await resolve_user_timezone(db, user_id) or "UTC")
    frozen_references = await freeze_image_references(
        {f"ref{index}": value for index, value in enumerate(reference_images)},
        user_id=user_id,
        generation_id=state.generation_id,
        directory=asset_directory,
    )
    params = _VideoJobParams(
        task=task,
        request_scope=request_scope,
        duration=duration,
        asset_directory=asset_directory,
        resolution=resolution,
        reference_images=tuple(frozen_references.values()),
        aspect_ratio=aspect_ratio,
        identity_reference_path=identity_reference_path,
        identity_snapshot=identity,
        identity_reference_role=identity_reference_role,
        asset_generation_id=state.generation_id,
    )
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
    session_id: str | None,
    prompt: str,
    source_message_id: int | None = None,
    request_scope: str | None = None,
    asset_directory: str | None = None,
    duration: int,
    resolution: str,
    aspect_ratio: str | None,
    reference_images: tuple[str, ...] = (),
    identity_reference_path: str | None = None,
    identity: VisualIdentitySnapshot | None = None,
    identity_reference_role: CharacterReferenceRole = "portrait",
    structured_reply: bool = False,
    media_id: str | None = None,
) -> VideoJobSnapshot:
    job = await prepare_video_job(
        db,
        user_id=user_id,
        task=task,
        session_id=session_id,
        prompt=prompt,
        source_message_id=source_message_id,
        request_scope=request_scope,
        asset_directory=asset_directory,
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images,
        identity_reference_path=identity_reference_path,
        identity=identity,
        identity_reference_role=identity_reference_role,
        structured_reply=structured_reply,
        media_id=media_id,
    )
    await _sync_changed_job_assets(db)
    await db.commit()
    return await submit_video_job(user_id, job.id)


def _spawn_poll(job_id: int, user_id: int) -> None:
    _POLL_TASKS.spawn(
        job_id,
        lambda: _poll_and_finalize(job_id),
        user_id=user_id,
        cancel_on_maintenance=False,
        on_error=_on_video_task_error,
    )


async def _record_failure(job_id: int, *, reason: str, settle_unknown: bool = False) -> None:
    """保存失败终态；聊天交付与状态同事务，错误文案不含供应商原文。终态不交付视频，已下载的成品与候选在提交后删除。"""
    logger.warning("video job failure", extra={"job_id": job_id, "reason": reason})
    user_msg = video_failure_message(reason)
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _abandoned(row):
            return
        if row.stage in {"review_pending", "confirmation"}:
            return
        if row.candidate_video_url and reason in {
            "quality_unavailable",
            "worker_failed",
            "identity_changed",
            "download_failed",
        }:
            row.state, row.stage, row.error = "paused", "review_pending", user_msg
            emit_ws_event(db, user_id=row.user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
            await db.commit()
            return
        # 结果未知可由用户核对后的供应商结论终结，其余终态不接受覆写。
        if row.state in _TERMINAL_STATES and not (settle_unknown and row.state == "unknown"):
            return
        existing = MediaChainState.model_validate(row.checkpoint or {})
        if row.candidate_video_url or row.video_url or existing.candidates or existing.result_url:
            row.state = (
                "unknown" if reason == "result_unknown" else "failed" if reason == "quality_failed" else "paused"
            )
            row.stage = (
                "result_unknown"
                if row.state == "unknown"
                else "failed"
                if row.state == "failed"
                else "confirmation"
                if reason == "identity_changed"
                else "review_pending"
            )
            row.error_reason, row.error = reason, user_msg
            await _sync_changed_job_assets(db)
            await db.flush()
            await deliver_video_result(db, await video_job_snapshot_async(db, row))
            emit_ws_event(db, user_id=row.user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
            await db.commit()
            return
        row.state, row.stage = (
            ("unknown" if reason == "result_unknown" else "failed"),
            "result_unknown" if reason == "result_unknown" else "failed",
        )
        row.error_reason = reason
        row.error = user_msg
        state = MediaChainState.model_validate(row.checkpoint or {})
        discarded = asset_paths([row.candidate_video_url, row.video_url, row.checkpoint], row.user_id)
        row.candidate_video_url = row.video_url = None
        state.candidates = []
        state.source_path = None
        row.checkpoint = state.model_dump(mode="json")
        await enqueue_asset_cleanup(db, row.user_id, discarded)
        await _sync_changed_job_assets(db)
        await db.flush()
        await deliver_video_result(db, await video_job_snapshot_async(db, row))
        await _sync_changed_job_assets(db)
        await db.commit()
    await cleanup_user_assets(row.user_id)


async def _finalize_best_video(job_id: int, *, warning: str | None = None) -> None:
    """保存合格资产，等待业务所有者按原授权完成交付。"""
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _settled(row):
            return
        params = _VideoJobParams.model_validate(row.params_json)
        snapshot = params.identity_snapshot
        if snapshot is not None and not await visual_identity_is_current(db, row.user_id, snapshot):
            row.state, row.stage, row.error = (
                "paused",
                "confirmation",
                "原任务身份已失效，候选已保留，不能按当前身份交付",
            )
            await db.commit()
            return
        storage_url = row.video_url
        if not storage_url or _stored_asset_file(row.user_id, storage_url) is None:
            raise RuntimeError("best video asset is unavailable")
        state = MediaChainState.model_validate(row.checkpoint or {})
        state.finish()
        best = state.best_qualified()
        required = (
            ("identity", "goal", "quality")
            if snapshot is not None or params.identity_reference_path
            else ("goal", "quality")
        )
        if (
            best is None
            or not review_passes(best.review, required)
            or not await asyncio.to_thread(review_media_intact, best.review.evidence, storage_url)
        ):
            raise RuntimeError("best video candidate is unavailable")
        if best.path != storage_url:
            raise RuntimeError("best video candidate does not match stored asset")
        row.review_result_json = best.review.model_dump(mode="json") if best.review else {}
        row.candidate_video_url = None
        row.state, row.stage = "paused", "confirmation"
        row.error_reason = state.stop_reason if warning else None
        row.error = warning
        row.checkpoint = state.model_dump(mode="json")
        await _sync_changed_job_assets(db)
        await db.flush()
        await deliver_video_result(db, await video_job_snapshot_async(db, row))
        emit_ws_event(db, user_id=row.user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
        await _sync_changed_job_assets(db)
        await db.commit()


async def _evaluate_stored_video(job_id: int, *, review_only: bool = False) -> str:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _abandoned(row):
            return "invalid"
        path = row.candidate_video_url or row.video_url
        if not path:
            return "invalid"
        params = _VideoJobParams.model_validate(row.params_json)
        user_id, prompt = row.user_id, row.prompt
        state = MediaChainState.model_validate(row.checkpoint or {})
        entry = next((c for c in state.candidates if c.path == path), None)
        if entry is None:
            entry = MediaCandidate(path=path, attempt=state.current_attempt_index())
            state.candidates.append(entry)
        row.checkpoint = state.model_dump(mode="json")
        await db.commit()
    local = _stored_asset_file(user_id, path)
    if local is None:
        return "invalid"
    try:
        await asyncio.to_thread(probe_video, local)
        samples = await asyncio.to_thread(sample_desktop_video_frames, local, sample_count=9)
    except Exception:
        return "invalid"
    uris = tuple(build_data_uri(frame, "image/webp") for frame in samples.frames)

    async def save_candidate() -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None or _abandoned(current) or current.state == "cancelled":
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
        entry.review = VisualReview(required=("goal", "quality"), evidence=evidence, status="unavailable")
        await save_candidate()

    async def before_submit() -> None:
        await _require_live_job(job_id)
        require_new_generation_call(user_id)

    needs_identity = params.identity_snapshot is not None or params.identity_reference_path is not None
    reference_count = len(params.reference_images) or int(needs_identity)
    frozen_references: tuple[str, ...] = ()
    if needs_identity and entry.evidence:
        identity_images = await read_review_evidence(entry.evidence)
        frozen_references, uris = identity_images[:reference_count], identity_images[reference_count:]
    if needs_identity and entry.identity_verdict not in {"pass", "fail"}:
        references = await materialize_image_references(
            {
                "identity": params.reference_images[0] if params.reference_images else params.identity_reference_path,
                "outfit": params.reference_images[1] if len(params.reference_images) > 1 else None,
            },
            user_id=user_id,
        )
        frozen_references = frozen_references or tuple(references.values())
        identity = await review_character_frames_with_references(
            user_id,
            frozen_references,
            uris,
            CHARACTER_MEDIA_SELF_VIDEO_REVIEW if len(references) > 1 else CHARACTER_MEDIA_VIDEO_REVIEW,
            task=params.task,
            before_submit=before_submit,
            evidence=entry.evidence,
            save_evidence=save_identity,
            media_path=path,
            frame_times_seconds=samples.times_seconds,
        )
        entry.identity_verdict, entry.identity_reason, entry.evidence = (
            identity.verdict,
            identity.reason,
            identity.evidence,
        )
        await save_candidate()
    if needs_review(entry.review):
        old = entry.review
        quality = await review_media_quality(
            user_id,
            (*frozen_references, *uris),
            reference_count=len(frozen_references),
            generation_prompt=prompt,
            media_path=path,
            frame_times_seconds=samples.times_seconds,
            task=params.task,
            evidence=ReviewEvidence.model_validate_json(entry.result_json)
            if entry.result_json
            else old.evidence
            if old
            else None,
            previous=old,
            save_evidence=save_quality,
            before_submit=before_submit,
        )
        if old:
            for name in ("goal", "quality"):
                saved = getattr(old, name)
                if saved is not None and saved.verdict in {"pass", "fail"}:
                    setattr(quality, name, saved)
        entry.result_json = quality.evidence.model_dump_json() if quality.evidence else None
        entry.review = quality
    if needs_identity:
        entry.review.required = ("identity", "goal", "quality")
        entry.review.identity = (
            ReviewDecision(verdict=entry.identity_verdict, reason=entry.identity_reason)
            if entry.identity_verdict and entry.identity_reason
            else None
        )
    if needs_identity:
        entry.review.evidence = merge_review_evidence(entry.evidence, entry.review.evidence)
    entry.evaluated, entry.accepted = True, entry.review.verdict == "pass"
    await save_candidate()
    async with SESSION_LOCAL() as db:
        current = await db.get(VideoGenJob, job_id, with_for_update=True)
        if current is None or _abandoned(current):
            return "invalid"
        state = MediaChainState.model_validate(current.checkpoint or {})
        if entry.review.status != "complete" or entry.review.verdict == "review":
            state.stop_reason = "review_pending"
            current.state, current.stage = "paused", "review_pending"
            current.error = "视频已保留，可以查看候选后采用或重新生成"
            decision = "paused"
        elif review_only:
            current.state, current.stage = "paused", "confirmation"
            current.error = None if entry.accepted else "视频未通过检查，原候选已保留"
            decision = "paused"
        elif entry.accepted:
            current.video_url, current.candidate_video_url = path, None
            state.phase = "ready"
            decision = "complete"
        elif state.next_index < len(state.providers):
            current.state, current.stage = "queued", "retry_pending"
            current.candidate_video_url = None
            state.phase = "ready"
            decision = "retry"
        else:
            current.state, current.stage = "paused", "review_pending"
            current.error = "视频未通过检查，原候选已保留"
            decision = "paused"
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
        state = MediaChainState.model_validate(row.checkpoint or {})
        entry = (
            state.best_qualified()
            or next((candidate for candidate in state.candidates if candidate.path == row.candidate_video_url), None)
            or next((candidate for candidate in state.candidates if needs_review(candidate.review)), None)
            or state.best()
        )
        review = (
            entry.review
            if entry
            else VisualReview.model_validate(row.review_result_json)
            if row.review_result_json
            else None
        )
        candidate_key = candidate_review_key(state.generation_id)
        basis = review_basis(
            review,
            entry.evidence if entry else None,
            candidate_key if entry or row.candidate_video_url or row.video_url or state.result_url else None,
        )
        ready = review is not None and review.verdict == "pass"
        return MediaJobView(
            id=row.id,
            kind=row.kind,
            state=row.state,
            stage=row.stage,
            revision=row.revision,
            required_slots=1,
            ready_slots=int(ready),
            complete=row.state == "succeeded",
            slots=[
                MediaSlotView(
                    slot=0,
                    media_id=row.media_id or str(row.id),
                    status="ready"
                    if row.state == "succeeded"
                    else "review_pending"
                    if row.stage == "review_pending"
                    else row.state,
                    review=review,
                    basis_id=basis,
                    candidate_basis_id=candidate_key,
                    can_review=row.stage == "review_pending" and basis is not None and needs_review(review),
                    url=asset_store.client_asset_url(row.video_url)
                    if row.state == "succeeded" and row.video_url
                    else None,
                    preview_url=asset_store.client_asset_url(entry.path) if entry else None,
                    error=row.error,
                    candidates=await manual_candidate_views(state.candidates, media_type="video"),
                    can_regenerate=row.state in {"paused", "failed"},
                )
            ],
        )


async def review_video_job(user_id: int, job_id: int, slot: int, expected: MediaReviewRequest) -> MediaJobView:
    if slot != 0:
        raise RequestError(status_code=400, message="无效的视频槽位")
    async with _result_lock(job_id):
        async with SESSION_LOCAL() as db:
            row = await _get_job(db, job_id, user_id)
            if (
                row is None
                or row.stage != "review_pending"
                or not review_request_matches(await video_review_view(user_id, job_id), expected)
            ):
                raise RequestError(status_code=409, message="检查依据或任务状态已变化")
            state = MediaChainState.model_validate(row.checkpoint or {})
            params = _VideoJobParams.model_validate(row.params_json)
            row.state, row.stage = "running", "reviewing"
            await db.commit()
        error = None
        try:
            if not row.candidate_video_url and not row.video_url and state.result_url:
                received = await _download_and_store(
                    state.result_url,
                    user_id=user_id,
                    job_id=job_id,
                    attempt=state.current_attempt_index(),
                    generation_id=params.asset_generation_id,
                    directory=params.asset_directory,
                )
                async with SESSION_LOCAL() as db:
                    current = await _get_job(db, job_id, user_id)
                    if current is None or _abandoned(current) or current.state != "running":
                        raise _JobSettledError()
                    current.candidate_video_url = received
                    await db.commit()
            await _evaluate_stored_video(job_id, review_only=True)
        except Exception:
            logger.warning("explicit video review interrupted", extra={"job_id": job_id}, exc_info=True)
            error = "原视频已保留，检查尚未完成"
        finally:
            async with SESSION_LOCAL() as db:
                current = await _get_job(db, job_id, user_id)
                if current and current.state == "running":
                    current.state, current.stage, current.error = (
                        "paused",
                        "review_pending",
                        error or "原视频暂时无法检查",
                    )
                    emit_ws_event(
                        db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)}
                    )
                    await db.commit()
    return await video_review_view(user_id, job_id)


async def manually_review_video(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    row = await db.get(VideoGenJob, job_id)
    original_params = dict(row.params_json)
    if row.conversation_id is not None:
        await authorize_media_waits(db, user_id, job_id, "chat.video")
    if request.slot != 0 or row.cleanup_requested_at:
        raise RequestError(status_code=409, message="原任务已失效")
    params = _VideoJobParams.model_validate(row.params_json)
    if params.identity_snapshot and not await visual_identity_is_current(db, user_id, params.identity_snapshot):
        raise RequestError(status_code=409, message="原任务身份已失效，不能按当前身份交付")
    if request.action == "regenerate":
        if _POLL_TASKS.running(job_id) is not None:
            raise RequestError(status_code=409, message="原任务正在收尾，请稍后重试")
        previous = MediaChainState.model_validate(row.checkpoint or {})
        if not previous.providers:
            raise RequestError(status_code=409, message="原任务缺少制作输入，无法重新生成")
        fresh = MediaChainState(providers=previous.providers, generation_id=uuid4().hex)
        params.asset_generation_id = fresh.generation_id
        row.params_json = params.model_dump(mode="json")
        row.checkpoint = fresh.model_dump(mode="json")
        row.candidate_video_url = row.video_url = None
        row.review_result_json = {}
        row.state, row.stage, row.error = "queued", "retry_pending", None
        row.error_reason = None
        await db.flush()
        await deliver_video_result(db, await video_job_snapshot_async(db, row))
        return ManualAction(restart_job_id=job_id, audit_context={"params_json": original_params})
    chain = MediaChainState.model_validate(row.checkpoint or {})
    selected = next(
        (candidate for candidate in chain.candidates if manual_candidate_id(candidate) == request.candidate_id), None
    )
    if selected is None:
        raise RequestError(status_code=409, message="所选候选已变化，请刷新")
    required = (
        ("identity", "goal", "quality")
        if params.identity_snapshot or params.identity_reference_path
        else ("goal", "quality")
    )
    await approve_candidate(user_id, selected, required, request, db=db)
    chain.candidates = [selected, *(candidate for candidate in chain.candidates if candidate is not selected)]
    row.checkpoint = chain.model_dump(mode="json")
    row.candidate_video_url = selected.path
    row.review_result_json = selected.review.model_dump(mode="json")
    row.state, row.stage, row.error = "paused", "confirmation", None
    await _sync_changed_job_assets(db)
    return ManualAction(audit_context={"params_json": original_params})


def _queue_owner(job: VideoGenJob, state: MediaChainState) -> VideoQueueOwner:
    return VideoQueueOwner(kind="media", user_id=job.user_id, job_id=job.id, generation_id=state.generation_id)


async def inspect_video_queue_owner(
    owner: VideoQueueOwner, before_submit: bool = True, request_id: str | None = None
) -> Literal["ready", "pending", "paused", "gone"]:
    """提交准入通过媒体所有者核对，队列不访问业务 ORM。"""
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, owner.job_id)
        if job is None or job.user_id != owner.user_id or _settled(job):
            return "gone"
        state = MediaChainState.model_validate(job.checkpoint or {})
        if state.generation_id != owner.generation_id:
            return "gone"
        if request_id and state.queue_request_id and state.queue_request_id != request_id:
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
        if not state.queue_request_id:
            return "pending"
    return "ready"


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
        if request_id is None and await resolve_frozen_media_provider(user_id, "video_gen", frozen) is None:
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or _abandoned(current) or current.state != "queued":
                    return False
                latest = MediaChainState.model_validate(current.checkpoint or {})
                if latest.next_index != index or latest.generation_id != state.generation_id:
                    return False
                latest.next_index += 1
                current.checkpoint = latest.model_dump(mode="json")
                await _sync_changed_job_assets(db)
                await db.commit()
            continue
        if request_id is None:
            request_id = await enqueue_video_attempt(
                owner,
                frozen,
                index,
                VideoGenRequest(
                    prompt=prompt,
                    duration=params.duration,
                    resolution=params.resolution,
                    reference_images=params.reference_images,
                    aspect_ratio=params.aspect_ratio,
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
        if state.queue_request_id != request_id:
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
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _settled(row):
            return
        keep_best = bool(row.video_url)
        user_id = row.user_id
        if keep_best:
            state = MediaChainState.model_validate(row.checkpoint or {})
            state.stop_reason = state.stop_reason or reason
            discarded = asset_paths([row.candidate_video_url, row.checkpoint], user_id) - {row.video_url}
            state.candidates = [entry for entry in state.candidates if entry.path == row.video_url]
            if state.source_path in discarded:
                state.source_path = None
            row.checkpoint = state.model_dump(mode="json")
            row.candidate_video_url = None
            await enqueue_asset_cleanup(db, user_id, discarded)
            await _sync_changed_job_assets(db)
            await db.commit()
    if keep_best:
        warning = (
            f"{video_failure_message(reason)}；已保留此前最佳视频"
            if reason in {"result_unknown", "content_policy_blocked"}
            else "自动重生成未能完成，已保留此前最佳视频"
        )
        await _finalize_best_video(job_id, warning=warning)
        await cleanup_user_assets(user_id)
    else:
        await _record_failure(job_id, reason=reason)


async def _poll_and_finalize(job_id: int) -> None:
    """后台主循环：供应商任务和本地候选均可恢复；仅未知提交禁止自动重发。"""
    with asset_write_context(inherit=False):
        await _poll_and_finalize_locked(job_id)


async def _poll_and_finalize_locked(job_id: int) -> None:
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
                await _poll_and_finalize_locked(job_id)
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
                if decision == "paused":
                    return
                if decision == "complete":
                    await _finalize_best_video(job_id)
                    return
                if decision == "stale":
                    await _record_failure(job_id, reason="identity_changed")
                    return
                if decision == "invalid":
                    await _fail_or_keep_best(job_id, "quality_failed")
                    return
                if await _submit_next_video(job_id, user_id):
                    await _poll_and_finalize_locked(job_id)
                return
        if (job.state == "running" and job.stage == "downloading") and state.result_url:
            try:
                storage_url = await _download_and_store(
                    state.result_url,
                    user_id=user_id,
                    job_id=job_id,
                    attempt=state.current_attempt_index(),
                    generation_id=params.asset_generation_id,
                    directory=params.asset_directory,
                )
            except Exception:
                logger.warning("known video result awaits download recovery", extra={"job_id": job_id}, exc_info=True)
                if job.video_url:
                    await _fail_or_keep_best(job_id, "download_failed")
                    return
                await _update_job(
                    job_id, error_reason="download_failed", error=video_failure_message("download_failed")
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
            await _poll_and_finalize_locked(job_id)
            return
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
            await _poll_and_finalize_locked(job_id)
        elif state.stop_reason:
            await _fail_or_keep_best(job_id, reason or state.stop_reason)
        elif await _submit_next_video(job_id, user_id):
            await _poll_and_finalize_locked(job_id)

    except _JobSettledError:
        return
    except Exception:
        logger.exception("unhandled exception in video poll worker", extra={"job_id": job_id})
        try:
            async with SESSION_LOCAL() as db:
                latest = await db.get(VideoGenJob, job_id)
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
        except Exception:
            logger.exception("could not update video job after worker error", extra={"job_id": job_id})


async def _download_and_store(
    download_url: str | None, *, user_id: int, job_id: int, attempt: int, generation_id: str, directory: str
) -> str:
    return await download_and_store_video(
        download_url,
        user_id=user_id,
        job_id=job_id,
        attempt=attempt,
        generation_id=generation_id,
        directory=directory,
        guard=lambda: _require_not_abandoned(job_id),
    )


async def resume_pending_video_jobs() -> None:
    """从供应商句柄或确定性本地路径恢复，绝不重复提交结果未知的付费任务。"""

    async with SESSION_LOCAL() as db:
        interrupted = list(
            await db.scalars(
                select(VideoGenJob).where(
                    VideoGenJob.state == "running", VideoGenJob.stage.in_(("evaluating", "reviewing"))
                )
            )
        )
        for current in interrupted:
            current.state, current.stage = "paused", "review_pending"
        await db.commit()
        jobs = (
            await db.execute(
                select(VideoGenJob.id, VideoGenJob.user_id).where(
                    VideoGenJob.cleanup_requested_at.is_(None),
                    (
                        VideoGenJob.state.in_(("queued",))
                        | (
                            (VideoGenJob.state == "running")
                            & VideoGenJob.stage.in_(("processing", "downloading", "evaluating"))
                        )
                    ),
                )
            )
        ).all()
    for job_id, user_id in jobs:
        _spawn_poll(job_id, user_id)
    if jobs:
        logger.info("Resumed pending video jobs", extra={"count": len(jobs)})


async def resume_user_video_jobs(user_id: int) -> None:
    """维护退出后只恢复安全状态；未知提交和明确放弃的任务不自动继续。"""
    async with SESSION_LOCAL() as db:
        interrupted = list(
            await db.scalars(
                select(VideoGenJob).where(
                    VideoGenJob.user_id == user_id,
                    VideoGenJob.state == "running",
                    VideoGenJob.stage.in_(("evaluating", "reviewing")),
                )
            )
        )
        for current in interrupted:
            current.state, current.stage = "paused", "review_pending"
        await db.commit()
        job_ids = list(
            (
                await db.scalars(
                    select(VideoGenJob.id).where(
                        VideoGenJob.user_id == user_id,
                        VideoGenJob.cleanup_requested_at.is_(None),
                        (
                            VideoGenJob.state.in_(("queued",))
                            | (
                                (VideoGenJob.state == "running")
                                & VideoGenJob.stage.in_(("processing", "downloading", "evaluating"))
                            )
                        ),
                    )
                )
            ).all()
        )
    for job_id in job_ids:
        _spawn_poll(job_id, user_id)


def _result_lock(job_id: int) -> asyncio.Lock:
    return _RESULT_LOCKS.setdefault(job_id, asyncio.Lock())


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


async def receive_video_result(user_id: int, job_id: int) -> VideoJobSnapshot:
    """只核对并接收已付费结果，不检查、生成或交付媒体。"""
    async with _result_lock(job_id):
        async with SESSION_LOCAL() as db:
            row = await _get_job(db, job_id, user_id)
        if row is None:
            raise ValueError("找不到视频任务")
        if (row.state in ("succeeded", "failed", "cancelled")) or _POLL_TASKS.running(job_id) is not None:
            return await video_job_snapshot_async(db, row)
        params = _VideoJobParams.model_validate(row.params_json)
        state = MediaChainState.model_validate(row.checkpoint or {})
        result_url = state.result_url
        if state.queue_request_id:
            result = await get_video_attempt_status(state.queue_request_id)
            if result is None or result.status not in ("succeeded", "failed"):
                return await video_job_snapshot_async(db, row)
            if result.status == "failed":
                await _record_failure(job_id, reason=video_provider_failure_reason(result.error), settle_unknown=True)
                await acknowledge_video_attempt(state.queue_request_id)
                async with SESSION_LOCAL() as db:
                    return await video_job_snapshot_async(db, await _get_job(db, job_id, user_id))
            result_url = result.download_url or result_url
        candidate = row.candidate_video_url or video_job_asset_path(
            user_id,
            job_id,
            state.current_attempt_index(),
            generation_id=params.asset_generation_id,
            directory=params.asset_directory,
        )
        local = _stored_asset_file(user_id, candidate)
        if local is None:
            if not result_url:
                return await video_job_snapshot_async(db, row)
            candidate = await _download_and_store(
                result_url,
                user_id=user_id,
                job_id=job_id,
                attempt=state.current_attempt_index(),
                generation_id=params.asset_generation_id,
                directory=params.asset_directory,
            )
            local = _stored_asset_file(user_id, candidate)
        if local is None:
            raise ValueError("视频成品尚未就绪")
        await asyncio.to_thread(probe_video, local)
        existing = next((item for item in state.candidates if item.path == candidate and item.evaluated), None)
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None or _abandoned(current) or (current.state in ("succeeded", "failed", "cancelled")):
                if current is None:
                    raise ValueError("视频任务已删除")
                return await video_job_snapshot_async(db, current)
            state.source_path = candidate
            state.result_url = result_url
            state.stop_reason = ""
            # 用户核对只消费这次已付费结果，检查失败不得启动下一供应商。
            state.next_index = len(state.providers)
            current.state, current.stage = (
                "paused",
                "confirmation"
                if existing and existing.review and existing.review.verdict != "review"
                else "review_pending",
            )
            current.candidate_video_url = candidate
            if existing is not None:
                current.video_url = candidate
            if not any(item.path == candidate for item in state.candidates):
                state.candidates.append(MediaCandidate(path=candidate, attempt=state.current_attempt_index()))
            current.checkpoint = state.model_dump(mode="json")
            current.error = "原视频已接收，可以查看候选后采用或重新生成"
            emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
            await db.flush()
            await deliver_video_result(db, await video_job_snapshot_async(db, current))
            await _sync_changed_job_assets(db)
            await db.commit()
        if state.queue_request_id:
            await acknowledge_video_attempt(state.queue_request_id)
        async with SESSION_LOCAL() as db:
            current = await _get_job(db, job_id, user_id)
            if current is None:
                raise ValueError("视频任务已删除")
            return await video_job_snapshot_async(db, current)


async def discard_video_job(db: AsyncSession, user_id: int, job_id: int) -> None:
    """调用方事务内失效并登记回收；业务引用由所属模块核对，提交后再取消供应商。"""
    row = await db.scalar(
        select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id).with_for_update()
    )
    if row is None:
        return
    if row.conversation_id is not None:
        raise ValueError("视频任务仍用于会话，不能作为动态成品放弃")
    row.cleanup_requested_at = utc_now()
    row.cleanup_pending = True
    row.state, row.stage = "cancelled", "discarded"
    row.error_reason = "user_discarded"
    row.error = "视频已放弃；远端取消结果由清理任务核实，不会重新提交"
    await enqueue_asset_cleanup(
        db, user_id, asset_paths([row.video_url, row.candidate_video_url, row.checkpoint], user_id)
    )


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
    if row.state != "succeeded" and (row.state != "paused" or row.stage != "confirmation"):
        return False
    state = MediaChainState.model_validate(row.checkpoint or {})
    chosen = state.best_qualified()
    required = (
        ("identity", "goal", "quality")
        if params.identity_snapshot or params.identity_reference_path
        else ("goal", "quality")
    )
    report = (
        chosen.review
        if chosen
        else VisualReview.model_validate(row.review_result_json)
        if row.review_result_json
        else None
    )
    if not review_passes(report, required):
        return False
    path = row.video_url if row.state == "succeeded" else chosen.path if chosen else None
    if path is None or not await asyncio.to_thread(review_media_intact, report.evidence, path):
        return False
    if row.state == "succeeded":
        return bool(row.video_url and _stored_asset_file(user_id, row.video_url))
    if chosen is None or _stored_asset_file(user_id, chosen.path) is None:
        return False
    row.video_url, row.candidate_video_url = chosen.path, None
    row.review_result_json = report.model_dump(mode="json")
    row.state, row.stage, row.error = "succeeded", "complete", None
    await db.flush()
    await deliver_video_result(db, await video_job_snapshot_async(db, row))
    return True
