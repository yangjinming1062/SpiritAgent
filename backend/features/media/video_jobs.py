"""聊天与动态共用的视频任务：提交、轮询、下载与身份评分；凭句柄或已落盘候选恢复。"""

import asyncio
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Literal
from weakref import WeakValueDictionary

from infrastructure.ai.error_classifier import LLMRuntimeError, classify_api_error
from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from infrastructure.ai.providers.base import ProviderConfig, VideoGenProvider, VideoGenRequest, VideoJobStatus
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.assets.asset_store import (
    build_data_uri,
    dated_asset_directory,
    save_video_job_asset_async,
    sniff_media_ext,
    video_job_asset_path,
)
from infrastructure.assets.media_download import download_media_result
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.assets.references import asset_paths
from infrastructure.assets.write_protection import asset_write_context
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from infrastructure.video_processing.ffmpeg import VideoToolUnavailableError, probe_video
from infrastructure.video_processing.process import sample_key_frames
from pydantic import BaseModel, ConfigDict, Field
from runtime.background import TaskBag
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.configuration.settings.timezone import resolve_user_timezone
from features.media.chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
    video_failure_message,
    video_provider_failure_reason,
)
from features.media.contracts import VideoJobSnapshot
from features.media.delivery import deliver_video_result
from features.media.identity_contracts import CharacterCardSnapshot
from features.media.identity_review import score_character_frames_with_references
from features.media.models import VideoGenJob
from features.media.prompts import CHARACTER_MEDIA_SELF_VIDEO_SCORE
from features.media.queries import video_job_snapshot
from features.media.reference_reader import (
    active_identity_reference,
    character_snapshot_is_current,
    render_character_identity,
)
from features.media.video_queue.contracts import VideoQueueInfo, VideoQueueOwner, VideoQueueStoppedError
from features.media.video_queue.service import (
    acknowledge_video_attempt,
    cancel_owner_video_attempts,
    enqueue_video_attempt,
    get_video_attempt_status,
    wait_video_attempt,
)

logger = get_logger(__name__)

_INFLIGHT: set[int] = set()
_POLL_TASKS: dict[int, asyncio.Task[None]] = {}
_RESULT_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

_BG = TaskBag("media.video_jobs")
_TERMINAL_STATUSES = ("succeeded", "failed", "result_unknown", "review_pending", "discarded")
_RESOLUTION_RANK = {"480P": 0, "720P": 1, "768P": 1, "1080P": 2, "2K": 3}


class _VideoJobParams(BaseModel):
    """任务冻结的请求参数与出镜身份。"""

    model_config = ConfigDict(extra="forbid")

    task: AiTaskKey
    duration: int
    resolution: str
    reference_images: tuple[str, ...] = ()
    aspect_ratio: str | None
    identity_reference_path: str | None
    identity_snapshot: CharacterCardSnapshot | None
    asset_generation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    asset_directory: str


def _abandoned(job: VideoGenJob) -> bool:
    """已登记清理的任务不再推进状态。"""
    return job.cleanup_requested_at is not None


def _settled(job: VideoGenJob) -> bool:
    """已登记清理或已到终态的任务不再接受状态覆写。"""
    return _abandoned(job) or job.status in _TERMINAL_STATUSES


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


async def _score_self_video(
    user_id: int,
    video_url: str,
    identity_path: str | None,
    *,
    task: AiTaskKey,
    identity_uri: str | None,
    outfit_uri: str | None = None,
    identity_text: str = "",
    job_id: int | None = None,
) -> tuple[str, int | None]:
    """核查候选可解码性，出镜时再评分。undecodable 表示成品本身不可解码，可换下一家；文件缺失、探测工具不可用不是成品问题，返回 invalid，仅非出镜视频在工具不可用时返回 unavailable 照常交付。"""
    if identity_path:
        async with SESSION_LOCAL() as db:
            current_seed = await active_identity_reference(db, user_id)
        if current_seed != identity_path:
            return "stale", None
    local = _stored_asset_file(user_id, video_url)
    if local is None:
        return "invalid", None
    try:
        await asyncio.to_thread(probe_video, local)
    except VideoToolUnavailableError:
        logger.warning("chat video probe tool unavailable", extra={"user_id": user_id}, exc_info=True)
        return ("invalid" if identity_path else "unavailable"), None
    except Exception:
        logger.warning("chat video cannot be decoded", extra={"user_id": user_id}, exc_info=True)
        return "undecodable", None
    if not identity_path:
        return "unavailable", None
    if not identity_uri or not outfit_uri:
        return "invalid", None
    try:
        frames = await asyncio.to_thread(sample_key_frames, local)

        async def before_submit() -> None:
            if job_id is not None:
                await _require_live_job(job_id)
            require_new_generation_call(user_id)

        frame_uris = tuple(build_data_uri(frame, "image/webp") for frame in frames)
        score = await score_character_frames_with_references(
            user_id,
            (identity_uri, outfit_uri),
            frame_uris,
            CHARACTER_MEDIA_SELF_VIDEO_SCORE,
            task=task,
            identity_text=identity_text,
            before_submit=before_submit,
        )
        async with SESSION_LOCAL() as db:
            latest_seed = await active_identity_reference(db, user_id)
        if latest_seed != identity_path:
            return "stale", None
        return "scored" if score is not None else "unavailable", score
    except (GenerationWorkPaused, _JobSettledError):
        raise
    except Exception:
        logger.warning("chat video identity scoring failed", extra={"user_id": user_id}, exc_info=True)
        return "unavailable", None


def _on_video_task_error(task: asyncio.Task) -> None:
    """视频后台任务失败落日志；poll 路径自身已记录详细原因,这里只兜底未捕获异常。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception("video background task failed", exc_info=exc)


async def drain() -> None:
    """取消并等待所有后台视频任务完成，吞下 CancelledError。"""
    await _BG.drain()


async def _update_job(job_id: int, **fields: object) -> None:
    """用全新短会话更新任务行——后台任务比请求会话长寿，绝不复用调用方的 ``db``；行在读写之间被 GC（管理员 DELETE 等）则提前返回。终态保护：succeeded/failed 行不再被覆写，避免晚到的 poll 失败翻转已成功状态。"""

    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id, with_for_update=True)
        if job is None:
            return
        if _settled(job):
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await db.commit()


async def _get_job(db: AsyncSession, job_id: int, user_id: int) -> VideoGenJob | None:
    """按 user_id 过滤：task_id 来自模型的工具参数，只能读到本用户的任务；会话归属由调用方核对。"""

    stmt = select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _compatible_video_configs(
    chain: list[ProviderConfig],
    *,
    reference_images: bool,
    duration: int,
    resolution: str,
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
            and _compatible_video_configs(
                chain,
                reference_images=reference_images,
                duration=duration,
                resolution=value,
            )
        },
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
            duration=duration,
            resolution=resolution,
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
    asset_directory: str | None = None,
    duration: int,
    resolution: str,
    aspect_ratio: str | None,
    reference_images: tuple[str, ...] = (),
    identity_reference_path: str | None = None,
    identity: CharacterCardSnapshot | None = None,
    structured_reply: bool = False,
    media_id: str | None = None,
) -> VideoJobSnapshot:
    """冻结能力与请求，在调用方事务内创建任务；提交供应商之前必须提交事务。"""
    if identity is not None and (not identity_reference_path or not reference_images):
        raise ValueError("Self video requires an identity reference and snapshot")
    chain = await resolve_provider_chain(db, user_id, "video_gen", task=task)
    compatible = _compatible_video_configs(
        chain,
        reference_images=bool(reference_images),
        duration=duration,
        resolution=resolution,
    )
    if not compatible:
        raise MissingLlmConfigError("未配置支持本次参考输入、时长和分辨率的视频供应商")
    state = MediaChainState(providers=[FrozenMediaProvider.from_config(config) for config in compatible])
    if not asset_directory:
        # 空串同缺省处理：按任务创建日冻结，不能把空目录写进任务参数落成根目录平铺。
        asset_directory = dated_asset_directory(utc_now(), await resolve_user_timezone(db, user_id) or "UTC")
    params = _VideoJobParams(
        task=task,
        duration=duration,
        asset_directory=asset_directory,
        resolution=resolution,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        identity_reference_path=identity_reference_path,
        identity_snapshot=identity,
        asset_generation_id=state.generation_id,
    )
    job = VideoGenJob(
        user_id=user_id,
        session_id=session_id,
        source_message_id=source_message_id,
        structured_reply=structured_reply,
        media_id=media_id,
        provider=compatible[0].provider_name,
        model=compatible[0].model,
        prompt=prompt,
        params_json=params.model_dump_json(),
        status="retry_pending",
        generation_state_json=state.model_dump_json(),
    )
    db.add(job)
    await db.flush()
    return video_job_snapshot(job)


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
        return video_job_snapshot(job)


async def enqueue_video_job(
    db: AsyncSession,
    *,
    user_id: int,
    task: AiTaskKey,
    session_id: str | None,
    prompt: str,
    source_message_id: int | None = None,
    asset_directory: str | None = None,
    duration: int,
    resolution: str,
    aspect_ratio: str | None,
    reference_images: tuple[str, ...] = (),
    identity_reference_path: str | None = None,
    identity: CharacterCardSnapshot | None = None,
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
        asset_directory=asset_directory,
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images,
        identity_reference_path=identity_reference_path,
        identity=identity,
        structured_reply=structured_reply,
        media_id=media_id,
    )
    await db.commit()
    return await submit_video_job(user_id, job.id)


def _spawn_poll(job_id: int, user_id: int) -> None:
    existing = _POLL_TASKS.get(job_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_poll_and_finalize(job_id))
    _POLL_TASKS[job_id] = task
    task.add_done_callback(lambda done: _POLL_TASKS.pop(job_id, None) if _POLL_TASKS.get(job_id) is done else None)
    _BG.add(task, on_error=_on_video_task_error)
    track_user_task(user_id, task, cancel_on_maintenance=False)


async def _record_failure(job_id: int, *, reason: str) -> None:
    """保存失败终态；聊天交付与状态同事务，错误文案不含供应商原文。终态不交付视频，已下载的成品与候选在提交后删除。"""
    logger.warning("video job failure", extra={"job_id": job_id, "reason": reason})
    user_msg = video_failure_message(reason)
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _settled(row):
            return
        row.status = "result_unknown" if reason == "result_unknown" else "failed"
        row.error_reason = reason
        row.error_message = user_msg
        state = MediaChainState.model_validate_json(row.generation_state_json)
        discarded = asset_paths([row.candidate_video_url, row.video_url, row.generation_state_json], row.user_id)
        row.candidate_video_url = row.video_url = None
        state.candidates = []
        state.source_path = None
        row.generation_state_json = state.model_dump_json()
        await enqueue_asset_cleanup(db, row.user_id, discarded)
        await deliver_video_result(db, video_job_snapshot(row))
        await db.commit()
    await cleanup_user_assets(row.user_id)


async def _finalize_best_video(job_id: int, *, warning: str | None = None) -> None:
    """保存最佳资产与终态；聊天交付同事务提交，重复恢复不再送达。"""
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _settled(row):
            return
        snapshot = _VideoJobParams.model_validate_json(row.params_json).identity_snapshot
        if snapshot is not None and not await character_snapshot_is_current(db, row.user_id, snapshot):
            await db.rollback()
            await _record_failure(job_id, reason="identity_changed")
            return
        storage_url = row.video_url
        if not storage_url or _stored_asset_file(row.user_id, storage_url) is None:
            raise RuntimeError("best video asset is unavailable")
        row.status = "succeeded"
        state = MediaChainState.model_validate_json(row.generation_state_json)
        state.finish()
        row.generation_state_json = state.model_dump_json()
        best = state.best()
        if best is None:
            raise RuntimeError("best video candidate is unavailable")
        row.provider = state.providers[best.attempt].provider
        row.model = state.providers[best.attempt].model
        row.candidate_video_url = None
        row.error_reason = state.stop_reason if warning else None
        row.error_message = warning
        await deliver_video_result(db, video_job_snapshot(row))
        await db.commit()


async def _evaluate_stored_video(job_id: int) -> str:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or _abandoned(row) or row.status != "evaluating" or not row.candidate_video_url:
            return "invalid"
        candidate = row.candidate_video_url
        params = _VideoJobParams.model_validate_json(row.params_json)
        user_id = row.user_id
    snapshot = params.identity_snapshot
    if snapshot is not None:
        async with SESSION_LOCAL() as db:
            if not await character_snapshot_is_current(db, user_id, snapshot):
                return "stale"
    if params.identity_reference_path and is_user_in_maintenance(user_id):
        return "paused"
    try:
        outcome, score = await _score_self_video(
            user_id,
            candidate,
            params.identity_reference_path,
            task=params.task,
            identity_uri=params.reference_images[0] if snapshot is not None else None,
            outfit_uri=params.reference_images[1]
            if snapshot is not None and len(params.reference_images) > 1
            else None,
            identity_text=render_character_identity(snapshot) if snapshot else "",
            job_id=job_id,
        )
    except GenerationWorkPaused:
        return "paused"
    if snapshot is not None:
        async with SESSION_LOCAL() as db:
            if not await character_snapshot_is_current(db, user_id, snapshot):
                return "stale"
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _abandoned(row) or row.status != "evaluating" or row.candidate_video_url != candidate:
            return "invalid"
        if outcome == "stale":
            return "stale"
        if outcome == "invalid":
            return "invalid"
        state = MediaChainState.model_validate_json(row.generation_state_json)
        if outcome == "undecodable":
            if not state.needs_next():
                return "invalid"
            logger.warning(
                "video candidate cannot be decoded; trying next provider",
                extra={"job_id": job_id, "chain_index": row.generation_attempt_index},
            )
            row.candidate_video_url = None
            row.status = "retry_pending"
            state.phase = "ready"
            if state.source_path == candidate:
                state.source_path = None
            row.generation_state_json = state.model_dump_json()
            await enqueue_asset_cleanup(db, user_id, [candidate])
            await db.commit()
            await cleanup_user_assets(user_id)
            return "retry"
        entry = MediaCandidate(path=candidate, attempt=row.generation_attempt_index)
        state.candidates.append(entry)
        state.accept_score(entry, score)
        best = state.best()
        discarded = {entry.path for entry in state.candidates if entry.path != best.path}
        state.candidates = [best]
        if state.source_path in discarded:
            state.source_path = None
        row.video_url = best.path
        row.candidate_video_url = None
        state.phase = "ready"
        decision = "retry" if state.needs_next() else "complete"
        if decision == "retry":
            row.status = "retry_pending"
        row.generation_state_json = state.model_dump_json()
        await enqueue_asset_cleanup(db, user_id, discarded)
        await db.commit()
    await cleanup_user_assets(user_id)
    return decision


def _queue_owner(job: VideoGenJob, state: MediaChainState) -> VideoQueueOwner:
    return VideoQueueOwner(kind="media", user_id=job.user_id, entity_id=job.id, generation_id=state.generation_id)


async def inspect_video_queue_owner(
    owner: VideoQueueOwner,
    before_submit: bool = True,
    request_id: str | None = None,
) -> Literal["ready", "pending", "paused", "gone"]:
    """提交准入通过媒体所有者核对，队列不访问业务 ORM。"""
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, owner.entity_id)
        if job is None or job.user_id != owner.user_id or _settled(job):
            return "gone"
        state = MediaChainState.model_validate_json(job.generation_state_json)
        if state.generation_id != owner.generation_id:
            return "gone"
        if request_id and state.queue_request_id and state.queue_request_id != request_id:
            return "gone"
        params = _VideoJobParams.model_validate_json(job.params_json)
        if (
            before_submit
            and params.identity_snapshot is not None
            and not await character_snapshot_is_current(
                db,
                job.user_id,
                params.identity_snapshot,
            )
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
            if row is None or _abandoned(row) or row.status != "retry_pending":
                return False
            state = MediaChainState.model_validate_json(row.generation_state_json)
            params = _VideoJobParams.model_validate_json(row.params_json)
            owner = _queue_owner(row, state)
            prompt = row.prompt
        if state.stop_reason or state.next_index >= len(state.providers):
            await _fail_or_keep_best(job_id, state.stop_reason or "provider_unavailable")
            return False
        index = state.next_index
        frozen = state.providers[index]
        config = await resolve_frozen_media_provider(user_id, "video_gen", frozen)
        if config is None:
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or _abandoned(current) or current.status != "retry_pending":
                    return False
                latest = MediaChainState.model_validate_json(current.generation_state_json)
                if latest.next_index != index or latest.generation_id != state.generation_id:
                    return False
                latest.next_index += 1
                current.generation_state_json = latest.model_dump_json()
                await db.commit()
            continue
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
            if current is None or _abandoned(current) or current.status != "retry_pending":
                return False
            latest = MediaChainState.model_validate_json(current.generation_state_json)
            if latest.next_index != index or latest.generation_id != state.generation_id:
                return False
            latest.begin(index)
            # processing 在业务层包含排队；只有队列实际发送才进入其 submitting 状态。
            latest.phase = "processing"
            latest.queue_request_id = request_id
            latest.video_queue = VideoQueueInfo(state="waiting", next_retry_at=None)
            current.status = "processing"
            current.generation_state_json = latest.model_dump_json()
            current.generation_attempt_index = index
            current.provider = config.provider_name
            current.model = config.model
            current.provider_task_id = None
            await db.commit()
        return True


async def _queue_progress(job_id: int, request_id: str, info: VideoQueueInfo | None) -> None:
    async with SESSION_LOCAL() as db:
        current = await db.get(VideoGenJob, job_id, with_for_update=True)
        if current is None or _settled(current):
            return
        state = MediaChainState.model_validate_json(current.generation_state_json)
        if state.queue_request_id != request_id or state.video_queue == info:
            return
        state.video_queue = info
        current.generation_state_json = state.model_dump_json()
        emit_ws_event(
            db,
            user_id=current.user_id,
            event_type="video_gen.queue",
            payload={
                "task_id": str(job_id),
                "session_id": current.session_id,
                "media_id": current.media_id,
                "video_queue": info.model_dump(mode="json") if info else None,
            },
        )
        await db.commit()


async def _wait_queued_video(job_id: int, state: MediaChainState) -> VideoJobStatus:
    request_id = state.queue_request_id
    if request_id is None:
        raise RuntimeError("Video task has no shared queue attempt")

    async def on_submitted(task_id: str) -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None:
                return
            latest = MediaChainState.model_validate_json(current.generation_state_json)
            if latest.queue_request_id != request_id:
                return
            # 业务放弃后仍保存句柄，清理不能误把远端工作当作未提交。
            current.provider_task_id = task_id
            await db.commit()

    async def on_queue(info: VideoQueueInfo | None) -> None:
        await _queue_progress(job_id, request_id, info)
        state.video_queue = info

    return await wait_video_attempt(request_id, on_submitted=on_submitted, on_queue=on_queue)


async def _fail_or_keep_best(job_id: int, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or _settled(row):
            return
        keep_best = bool(row.video_url)
        user_id = row.user_id
        if keep_best:
            state = MediaChainState.model_validate_json(row.generation_state_json)
            state.stop_reason = state.stop_reason or reason
            discarded = asset_paths([row.candidate_video_url, row.generation_state_json], user_id) - {row.video_url}
            state.candidates = [entry for entry in state.candidates if entry.path == row.video_url]
            if state.source_path in discarded:
                state.source_path = None
            row.generation_state_json = state.model_dump_json()
            row.candidate_video_url = None
            await enqueue_asset_cleanup(db, user_id, discarded)
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


# 进程内 in-flight 集合：并发 finalize 同一任务时只有第一个进入，避免重复下载/发事件；重启后由 resume_pending_video_jobs 按 DB 重建。
async def _poll_and_finalize(job_id: int) -> None:
    """后台主循环：供应商任务和本地候选均可恢复；仅未知提交禁止自动重发。"""
    if job_id in _INFLIGHT:
        return
    _INFLIGHT.add(job_id)
    try:
        with asset_write_context(inherit=False):
            await _poll_and_finalize_locked(job_id)
    finally:
        _INFLIGHT.discard(job_id)


async def _poll_and_finalize_locked(job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
        if job is None:
            return
        # 幂等护栏：上一次已终结的行不再重复下载/发事件；``_update_job`` 内部的终态检查是双保险。
        if _settled(job):
            logger.info("skipping already-finalized job", extra={"job_id": job_id, "status": job.status})
            return
        user_id = job.user_id
        params = _VideoJobParams.model_validate_json(job.params_json)

    try:
        if job.status == "retry_pending":
            if await _submit_next_video(job_id, user_id):
                await _poll_and_finalize_locked(job_id)
            return
        state = MediaChainState.model_validate_json(job.generation_state_json)
        if job.status == "evaluating" and state.phase == "ready" and not job.candidate_video_url and job.video_url:
            await _finalize_best_video(job_id)
            return
        if job.status in ("downloading", "evaluating"):
            candidate_path = job.candidate_video_url
            if not candidate_path:
                candidate_path = video_job_asset_path(
                    user_id,
                    job_id,
                    job.generation_attempt_index,
                    generation_id=params.asset_generation_id,
                    directory=params.asset_directory,
                )
                if _stored_asset_file(user_id, candidate_path) is None:
                    candidate_path = None
            if candidate_path:
                await _update_job(job_id, status="evaluating", candidate_video_url=candidate_path)
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
        if job.status == "downloading" and state.result_url:
            try:
                storage_url = await _download_and_store(
                    state.result_url,
                    user_id=user_id,
                    job_id=job_id,
                    attempt=job.generation_attempt_index,
                    generation_id=params.asset_generation_id,
                    directory=params.asset_directory,
                )
            except Exception:
                logger.warning("known video result awaits download recovery", extra={"job_id": job_id}, exc_info=True)
                if job.video_url:
                    await _fail_or_keep_best(job_id, "download_failed")
                    return
                await _update_job(
                    job_id,
                    error_reason="download_failed",
                    error_message=video_failure_message("download_failed"),
                )
                return
            await _update_job(
                job_id,
                status="evaluating",
                candidate_video_url=storage_url,
                error_reason=None,
                error_message=None,
            )
            await _poll_and_finalize_locked(job_id)
            return
        if job.status == "evaluating" and job.video_url:
            await _finalize_best_video(job_id)
            return

        if not state.queue_request_id:
            # 制作状态必须对应持久队列；缺失引用时保留未知结果，防止重发。
            await _fail_or_keep_best(job_id, "result_unknown")
            return
        try:
            status = await _wait_queued_video(job_id, state)
        except asyncio.CancelledError:
            raise
        except GenerationWorkPaused:
            return
        except (LLMRuntimeError, VideoQueueStoppedError) as exc:
            reason, can_continue = media_failure_reason(exc)
            classified = classify_api_error(exc)
            logger.warning(
                "queued video attempt failed",
                extra={
                    "job_id": job_id,
                    "reason": reason,
                    "can_continue": can_continue,
                    "status_code": classified.status_code,
                    "error": classified.message,
                },
            )
            request_id = state.queue_request_id
            state.video_queue = None
            state.queue_request_id = None
            state.phase = "ready"
            if not can_continue or state.next_index >= len(state.providers):
                state.stop_reason = reason
            await _update_job(job_id, status="retry_pending", generation_state_json=state.model_dump_json())
            await acknowledge_video_attempt(request_id)
            if await _submit_next_video(job_id, user_id):
                await _poll_and_finalize_locked(job_id)
            return

        if status.status == "succeeded":
            request_id = state.queue_request_id
            state.phase = "storing"
            state.result_url = status.download_url
            state.queue_request_id = None
            state.video_queue = None
            if not state.result_url:
                await _fail_or_keep_best(job_id, "download_failed")
                await acknowledge_video_attempt(request_id)
                return
            if job.status != "downloading":
                async with SESSION_LOCAL() as db:
                    claimed = (
                        await db.execute(
                            update(VideoGenJob)
                            .where(
                                VideoGenJob.id == job_id,
                                VideoGenJob.status == "processing",
                                VideoGenJob.cleanup_requested_at.is_(None),
                            )
                            .values(status="downloading", generation_state_json=state.model_dump_json()),
                        )
                    ).rowcount
                    await db.commit()
                if not claimed:
                    return
            else:
                await _update_job(job_id, generation_state_json=state.model_dump_json())
            await acknowledge_video_attempt(request_id)
            await _poll_and_finalize_locked(job_id)
            return
        if state.needs_next():
            request_id = state.queue_request_id
            state.queue_request_id = None
            state.video_queue = None
            state.phase = "ready"
            await _update_job(job_id, status="retry_pending", generation_state_json=state.model_dump_json())
            await acknowledge_video_attempt(request_id)
            if await _submit_next_video(job_id, user_id):
                await _poll_and_finalize_locked(job_id)
            return
        await _fail_or_keep_best(job_id, video_provider_failure_reason(status.error))
        await acknowledge_video_attempt(state.queue_request_id)
    except _JobSettledError:
        return
    except Exception:
        logger.exception("unhandled exception in video poll worker", extra={"job_id": job_id})
        try:
            async with SESSION_LOCAL() as db:
                latest = await db.get(VideoGenJob, job_id)
            if latest is not None and latest.status == "processing":
                latest_state = MediaChainState.model_validate_json(latest.generation_state_json)
                if latest_state.queue_request_id:
                    logger.warning("queued video awaits recovery", extra={"job_id": job_id})
                    return
            if latest is not None and latest.status in ("downloading", "evaluating"):
                latest_params = _VideoJobParams.model_validate_json(latest.params_json)
                candidate = latest.candidate_video_url or video_job_asset_path(
                    user_id,
                    job_id,
                    latest.generation_attempt_index,
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
    download_url: str | None,
    *,
    user_id: int,
    job_id: int,
    attempt: int,
    generation_id: str,
    directory: str,
) -> str:
    """有界重试下载供应商成品并原子写入确定性路径，返回裸存储路径。只对传输错误与服务端 5xx 退避重试，超限、4xx 与过期地址等确定性失败直接上抛；大小上限由 ``video_gen_download_max_bytes`` 定义。"""
    if not download_url:
        raise RuntimeError("video result has no download url")
    await _require_not_abandoned(job_id)
    data = await download_media_result(download_url, max_bytes=SETTINGS.video_gen_download_max_bytes)
    await _require_not_abandoned(job_id)
    if sniff_media_ext(data) != "mp4":
        raise RuntimeError("provider returned a payload that is not an mp4 stream")
    return await save_video_job_asset_async(
        data,
        user_id=user_id,
        job_id=job_id,
        attempt=attempt,
        generation_id=generation_id,
        directory=directory,
    )


async def resume_pending_video_jobs() -> None:
    """从供应商句柄或确定性本地路径恢复，绝不重复提交结果未知的付费任务。"""

    async with SESSION_LOCAL() as db:
        jobs = (
            await db.execute(
                select(VideoGenJob.id, VideoGenJob.user_id).where(
                    VideoGenJob.cleanup_requested_at.is_(None),
                    VideoGenJob.status.in_(
                        ("processing", "downloading", "evaluating", "retry_pending"),
                    ),
                ),
            )
        ).all()
    for job_id, user_id in jobs:
        _spawn_poll(job_id, user_id)
    if jobs:
        logger.info("Resumed pending video jobs", extra={"count": len(jobs)})


async def resume_user_video_jobs(user_id: int) -> None:
    """维护退出后只恢复安全状态；未知提交和明确放弃的任务不自动继续。"""
    async with SESSION_LOCAL() as db:
        job_ids = list(
            (
                await db.scalars(
                    select(VideoGenJob.id).where(
                        VideoGenJob.user_id == user_id,
                        VideoGenJob.cleanup_requested_at.is_(None),
                        VideoGenJob.status.in_(("processing", "downloading", "evaluating", "retry_pending")),
                    ),
                )
            ).all(),
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
                    VideoGenJob.user_id == user_id,
                    VideoGenJob.cleanup_requested_at.is_not(None),
                ),
            ),
        )
    for job_id in ids:
        async with _result_lock(job_id):
            task = _POLL_TASKS.get(job_id)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            async with SESSION_LOCAL() as db:
                row = await db.get(VideoGenJob, job_id)
                if row is None or row.user_id != user_id or row.cleanup_requested_at is None:
                    continue
                params = _VideoJobParams.model_validate_json(row.params_json)
                state = MediaChainState.model_validate_json(row.generation_state_json)
            await cancel_owner_video_attempts("media", job_id, state.generation_id)
            paths = asset_paths([row.video_url, row.candidate_video_url, row.generation_state_json], user_id)
            attempts = {row.generation_attempt_index, *(entry.attempt for entry in state.candidates)}
            paths.update(
                video_job_asset_path(
                    user_id,
                    job_id,
                    attempt,
                    generation_id=params.asset_generation_id,
                    directory=params.asset_directory,
                )
                for attempt in attempts
            )
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or current.user_id != user_id or current.cleanup_requested_at is None:
                    continue
                await enqueue_asset_cleanup(db, user_id, paths)
                await db.delete(current)
                await db.commit()


async def cleanup_user_media(user_id: int) -> None:
    """历史变更提交后的统一收尾：先回收失效视频任务（含远端撤销），再删除无引用文件。"""
    await cleanup_user_video_jobs(user_id)
    await cleanup_user_assets(user_id)


def post_video_ready(job: VideoJobSnapshot) -> bool:
    return job.id not in _INFLIGHT and job.candidate_ready


async def query_post_video_job(user_id: int, job_id: int) -> VideoJobSnapshot:
    """用户显式查询原句柄或下载原成品；不提交生成、不推进供应商链、不调用模型评分。"""
    async with _result_lock(job_id):
        async with SESSION_LOCAL() as db:
            row = await _get_job(db, job_id, user_id)
        if row is None or row.session_id is not None:
            raise ValueError("找不到动态视频任务")
        if row.status in ("succeeded", "failed", "discarded") or job_id in _INFLIGHT:
            return video_job_snapshot(row)
        params = _VideoJobParams.model_validate_json(row.params_json)
        state = MediaChainState.model_validate_json(row.generation_state_json)
        candidate = (
            row.candidate_video_url
            or row.video_url
            or video_job_asset_path(
                user_id,
                job_id,
                row.generation_attempt_index,
                generation_id=params.asset_generation_id,
                directory=params.asset_directory,
            )
        )
        local = _stored_asset_file(user_id, candidate)
        if local is not None and candidate:
            await asyncio.to_thread(probe_video, local)
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or _abandoned(current) or current.status == "discarded":
                    raise ValueError("视频任务已放弃")
                state.source_path = candidate
                current.candidate_video_url = candidate
                current.status = "review_pending"
                current.generation_state_json = state.model_dump_json()
                await db.commit()
                return video_job_snapshot(current)
        result_url = state.result_url
        if not result_url and state.queue_request_id:
            result = await get_video_attempt_status(state.queue_request_id)
            if result is None:
                return video_job_snapshot(row)
            if result.status == "failed":
                async with SESSION_LOCAL() as db:
                    current = await db.get(VideoGenJob, job_id, with_for_update=True)
                    if current is not None and current.user_id == user_id and current.status != "discarded":
                        current.status = "failed"
                        current.error_reason = video_provider_failure_reason(result.error)
                        current.error_message = video_failure_message(current.error_reason)
                        await db.commit()
                        if state.queue_request_id:
                            await acknowledge_video_attempt(state.queue_request_id)
                        return video_job_snapshot(current)
            if result.status != "succeeded":
                return video_job_snapshot(row)
            result_url = result.download_url
        if not result_url:
            return video_job_snapshot(row)
        stored = await _download_and_store(
            result_url,
            user_id=user_id,
            job_id=job_id,
            attempt=row.generation_attempt_index,
            generation_id=params.asset_generation_id,
            directory=params.asset_directory,
        )
        local = _stored_asset_file(user_id, stored)
        if local is None:
            raise ValueError("视频成品尚未就绪")
        await asyncio.to_thread(probe_video, local)
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None or current.user_id != user_id or current.status == "discarded":
                raise ValueError("视频任务已放弃")
            current.candidate_video_url = stored
            current.status = "review_pending"
            state.result_url = result_url
            state.source_path = stored
            current.generation_state_json = state.model_dump_json()
            await db.commit()
            if state.queue_request_id:
                await acknowledge_video_attempt(state.queue_request_id)
            return video_job_snapshot(current)


async def post_video_asset(db: AsyncSession, user_id: int, job_id: int) -> str:
    """核对原成品与身份，在发布同一事务中结束原任务；不触发任何制作。"""
    row = await db.scalar(
        select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id).with_for_update(),
    )
    if row is None or row.session_id is not None or row.status in ("failed", "discarded"):
        raise ValueError("视频成品不可采纳")
    if job_id in _INFLIGHT:
        raise ValueError("视频任务仍在运行，请等待完成后再采纳")
    params = _VideoJobParams.model_validate_json(row.params_json)
    if params.identity_snapshot is not None and not await character_snapshot_is_current(
        db,
        user_id,
        params.identity_snapshot,
    ):
        raise ValueError("伙伴外形已更新，不能采纳旧参考视频")
    path = row.video_url or row.candidate_video_url
    if not path or _stored_asset_file(user_id, path) is None:
        raise ValueError("视频成品尚未就绪，请先查询原任务")
    state = MediaChainState.model_validate_json(row.generation_state_json)
    if row.status != "succeeded":
        if state.source_path != path:
            raise ValueError("请先查询并核对原视频成品")
        if not any(entry.path == path for entry in state.candidates):
            state.candidates.append(MediaCandidate(path=path, attempt=row.generation_attempt_index))
        state.stop_reason = "user_adopted"
        state.phase = "complete"
        row.video_url = path
        row.candidate_video_url = None
        row.status = "succeeded"
        row.error_reason = row.error_message = None
        row.generation_state_json = state.model_dump_json()
    return path


async def discard_video_job(db: AsyncSession, user_id: int, job_id: int) -> None:
    """调用方事务内失效并登记回收；业务引用由所属模块核对，提交后再取消供应商。"""
    row = await db.scalar(
        select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id).with_for_update(),
    )
    if row is None:
        return
    if row.session_id is not None:
        raise ValueError("视频任务仍用于会话，不能作为动态成品放弃")
    row.cleanup_requested_at = utc_now()
    row.status = "discarded"
    row.error_reason = "user_discarded"
    row.error_message = "视频已放弃；远端取消结果由清理任务核实，不会重新提交"
    await enqueue_asset_cleanup(
        db,
        user_id,
        asset_paths([row.video_url, row.candidate_video_url, row.generation_state_json], user_id),
    )
