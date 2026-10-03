"""聊天与动态共用的视频任务：提交、轮询、下载与身份评分；凭句柄或已落盘候选恢复。"""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Collection, Sequence
from datetime import timedelta
from pathlib import Path
from weakref import WeakValueDictionary

from components import (
    SESSION_LOCAL,
    SETTINGS,
    TaskBag,
    backoff_for_poll,
    get_logger,
    is_user_in_maintenance,
    redact_sensitive_text,
    track_user_task,
    utc_now,
)
from modules.channels import ChannelDelivery, ChannelDeliveryPayload, ChannelPeer, ChannelTurnSource
from modules.companion import AvatarAsset, CharacterCardSnapshot, CompanionAction, CompanionPost, PostPublication
from modules.conversation import Conversation, Message
from modules.media import VideoGenJob
from modules.ws import emit_ws_event
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import character_snapshot_is_current, render_character_identity
from services.domains.conversation import MEDIA_FAILURE_SUBTYPE, MEDIA_STATUS_SUBTYPE, update_video_reply
from services.infrastructure.assets import (
    VIDEO_DOWNLOAD_ATTEMPTS,
    asset_store,
    build_data_uri,
    client_asset_url,
    download_media_result,
    save_video_job_asset_async,
    sniff_media_ext,
    unlink_companion_asset,
    video_job_asset_path,
)
from services.infrastructure.llm import (
    FailoverReason,
    MissingLlmConfigError,
    ProviderConfig,
    ProviderResultUnknownError,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
    build_provider,
    classify_api_error,
    execute_with_fallback,
    resolve_provider_chain,
)
from services.infrastructure.video_processing import VideoToolUnavailableError, probe_video, sample_key_frames

from .avatar_service import load_avatar_bytes_as_data_uri
from .identity_review import score_character_frames
from .media_chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
    video_failure_message,
    video_provider_failure_reason,
)
from .paid_work import GenerationAuthorizationRevoked, GenerationWorkPaused, require_video_generation_call

logger = get_logger(__name__)

_INFLIGHT: set[int] = set()
_POLL_TASKS: dict[int, asyncio.Task[None]] = {}
_RESULT_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

_BG = TaskBag("media.video_jobs")
_TERMINAL_STATUSES = ("succeeded", "failed", "result_unknown", "review_pending", "discarded")
# 供应商任务可能仍在进行或已计费，终态记为 result_unknown 而非可重试的失败。
_RESULT_UNKNOWN_REASONS = frozenset({"result_unknown", "submit_result_unknown", "timeout"})


class _VideoJobParams(BaseModel):
    """任务冻结的请求参数与出镜身份。"""

    model_config = ConfigDict(extra="forbid")

    duration: int
    resolution: str
    first_frame_image: str | None
    aspect_ratio: str | None
    identity_reference_path: str | None
    identity_snapshot: CharacterCardSnapshot | None
    identity_reference: str | None
    # 老任务沿用原路径，新增任务冻结随机ID，避免恢复后数据库序列与旧文件重名。
    asset_generation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    channel_source: ChannelTurnSource | None = None


class VideoPollTimeoutError(RuntimeError):
    """供应商任务在轮询预算内未到终态。"""


class _JobSettledError(Exception):
    """轮询期间任务行已删除或已由其他路径终结。"""


async def poll_video_task(
    provider: VideoGenProvider,
    task_id: str,
    *,
    before_poll: Callable[[], Awaitable[None]] | None = None,
) -> VideoJobStatus:
    """有界退避轮询到 succeeded / failed；状态变化时重置退避。查询异常不终结已付费任务，退避后在同一时限内续查；鉴权与计费失败在时限内不会自愈（供应商实例已固定）直接上抛；before_poll 抛 _JobSettledError 中止轮询。"""
    deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
    attempt = 0
    last_status: str | None = None
    log_extra = {"provider": provider.config.provider_name, "model": provider.config.model, "task_id": task_id}
    while True:
        remaining = max(0.0, (deadline - utc_now()).total_seconds())
        if remaining <= 0:
            raise VideoPollTimeoutError
        try:
            if before_poll is not None:
                await before_poll()
            status = await provider.poll(task_id)
        except _JobSettledError:
            raise
        except Exception as exc:
            if classify_api_error(exc).reason in (FailoverReason.auth, FailoverReason.billing):
                raise
            logger.warning("video task poll failed; retrying", extra=log_extra, exc_info=True)
            current = "poll_error"
        else:
            if status.status == "failed":
                logger.warning(
                    "video task failed at provider",
                    extra={**log_extra, "error": redact_sensitive_text(status.error)},
                )
            if status.status in ("succeeded", "failed"):
                return status
            current = status.status
        if last_status is not None and current != last_status:
            attempt = 0
        elif last_status is not None:
            attempt += 1
        last_status = current
        sleep_for = backoff_for_poll(
            attempt,
            base_interval=SETTINGS.video_gen_poll_interval_seconds,
            max_interval=SETTINGS.video_gen_poll_backoff_max_seconds,
            remaining_seconds=remaining,
        )
        if sleep_for <= 0:
            raise VideoPollTimeoutError
        await asyncio.sleep(sleep_for)


def _stored_asset_file(user_id: int, storage_path: str | None) -> Path | None:
    """本用户裸资产路径对应的已存在文件。"""
    parsed = asset_store.parse_companion_asset_path(storage_path)
    if parsed is None or parsed[0] != user_id:
        return None
    resolved = asset_store.resolve_companion_asset_path(*parsed)
    return resolved[0] if resolved is not None else None


def video_generation_wait_seconds(job: VideoGenJob) -> float:
    state = MediaChainState.model_validate_json(job.generation_state_json)
    per_provider = (
        SETTINGS.video_gen_max_poll_seconds + VIDEO_DOWNLOAD_ATTEMPTS * 600 + 2 * SETTINGS.llm_request_timeout_seconds
    )
    return max(1, len(state.providers)) * per_provider + 90


async def _score_self_video(
    user_id: int,
    video_url: str,
    identity_path: str | None,
    *,
    identity_uri: str | None = None,
    identity_text: str = "",
    channel_source: ChannelTurnSource | None = None,
) -> tuple[str, int | None]:
    """核查候选可解码性，出镜时再评分。undecodable 表示成品本身不可解码，可换下一家；文件缺失、探测工具不可用不是成品问题，返回 invalid，仅非出镜视频在工具不可用时返回 unavailable 照常交付。"""
    if identity_path:
        async with SESSION_LOCAL() as db:
            current_seed = await db.scalar(
                select(AvatarAsset.seed_fullbody_url).where(
                    AvatarAsset.user_id == user_id,
                    AvatarAsset.active.is_(True),
                ),
            )
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
    try:
        identity_uri = identity_uri or await asyncio.to_thread(load_avatar_bytes_as_data_uri, identity_path)
    except Exception:
        logger.warning("chat video identity reference unavailable", extra={"user_id": user_id}, exc_info=True)
        return "unavailable", None
    try:
        frames = await asyncio.to_thread(sample_key_frames, local)
        score = await score_character_frames(
            user_id,
            identity_uri,
            tuple(build_data_uri(frame, "image/webp") for frame in frames),
            identity_text=identity_text,
            before_submit=lambda: require_video_generation_call(user_id, channel_source),
        )
        async with SESSION_LOCAL() as db:
            latest_seed = await db.scalar(
                select(AvatarAsset.seed_fullbody_url).where(
                    AvatarAsset.user_id == user_id,
                    AvatarAsset.active.is_(True),
                ),
            )
        if latest_seed != identity_path:
            return "stale", None
        return "scored" if score is not None else "unavailable", score
    except (GenerationWorkPaused, GenerationAuthorizationRevoked):
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
        if job.status in _TERMINAL_STATUSES:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await db.commit()


async def _add_channel_delivery(
    db: AsyncSession,
    job: VideoGenJob,
    *,
    text: str,
    media: list[dict[str, str]],
) -> None:
    source = _VideoJobParams.model_validate_json(job.params_json).channel_source
    if source is None or not await ChannelPeer.authorizes(db, job.user_id, source, lock=True):
        return
    db.add(
        ChannelDelivery(
            binding_id=source.binding_id,
            peer_id=source.peer_id,
            payload_json=ChannelDeliveryPayload.model_validate(
                {"text": text, "media": media, "channel_source": source},
            ).model_dump_json(),
        ),
    )


async def get_job(db: AsyncSession, job_id: int, user_id: int) -> VideoGenJob | None:
    """按 user_id 过滤：task_id 来自模型的工具参数，只能读到本用户的任务；会话归属由调用方核对。"""

    stmt = select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _compatible_video_configs(
    chain: list[ProviderConfig],
    *,
    first_frame: bool,
    duration: int,
    resolution: str,
) -> list[ProviderConfig]:
    compatible = []
    for config in chain:
        provider = build_provider(config, VideoGenProvider)
        if first_frame and not provider.supports_first_frame:
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
    first_frame: bool,
    duration: int,
    resolution: str,
    allowed_durations: Collection[int] | None = None,
    allowed_resolutions: Collection[str] | None = None,
) -> None:
    """付费生成首帧之前确认视频供应商链里有能接单的：首帧、时长与分辨率提交前才核对，首帧图已经花了钱却发现没有供应商能用。不满足时抛 MissingLlmConfigError，并在调用方允许的取值内说明可选的时长与分辨率。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen")
    if not chain:
        raise MissingLlmConfigError("视频生成服务未配置")
    if _compatible_video_configs(chain, first_frame=first_frame, duration=duration, resolution=resolution):
        return
    providers = [build_provider(config, VideoGenProvider) for config in chain]
    candidates = [p for p in providers if p.supports_first_frame or not first_frame]
    if not candidates:
        raise MissingLlmConfigError("已配置的视频供应商都不支持以首帧图生成视频")
    hints = []
    if not any(p.durations is None or duration in p.durations for p in candidates):
        options = sorted({d for p in candidates for d in p.durations or () if d in (allowed_durations or (d,))})
        hints.append("可选时长（秒）：" + "、".join(map(str, options)))
    if not any(
        p.resolutions is None or resolution.lower() in {value.lower() for value in p.resolutions} for p in candidates
    ):
        options = sorted(
            {
                v.upper()
                for p in candidates
                for v in p.resolutions or ()
                if v.upper() in (allowed_resolutions or (v.upper(),))
            },
        )
        hints.append("可选分辨率：" + "、".join(options))
    detail = "；".join(hints) or "同一供应商不同时支持所选时长与分辨率，请调整其一"
    raise MissingLlmConfigError(f"已配置的视频供应商不支持本次的时长或分辨率（{detail}）")


async def select_video_resolution(
    user_id: int,
    *,
    first_frame: bool,
    duration: int,
    preferred: Sequence[str],
) -> str | None:
    """按偏好顺序返回供应商链里有能接单的第一档分辨率，大小写写法沿用首个可接单供应商的声明；没有则返回 None，由调用方在付费前处理。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen")
    for resolution in preferred:
        compatible = _compatible_video_configs(chain, first_frame=first_frame, duration=duration, resolution=resolution)
        if compatible:
            declared = build_provider(compatible[0], VideoGenProvider).resolutions or ()
            return next((value for value in declared if value.lower() == resolution.lower()), resolution)
    return None


async def enqueue_video_job(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: str | None,
    prompt: str,
    duration: int,
    resolution: str,
    first_frame_image: str | None,
    aspect_ratio: str | None,
    identity_reference_path: str | None = None,
    identity: CharacterCardSnapshot | None = None,
    structured_reply: bool = False,
    media_id: str | None = None,
    channel_source: ChannelTurnSource | None = None,
) -> "VideoGenJob":
    """冻结能力链并提交首个任务；轮询绑定实际接单供应商，低分才推进链尾。"""
    chain = await resolve_provider_chain(db, user_id, "video_gen")
    compatible = _compatible_video_configs(
        chain,
        first_frame=bool(first_frame_image),
        duration=duration,
        resolution=resolution,
    )
    if not compatible:
        raise MissingLlmConfigError("未配置支持本次首帧、时长和分辨率的视频供应商")
    state = MediaChainState(providers=[FrozenMediaProvider.from_config(config) for config in compatible])
    params = _VideoJobParams(
        duration=duration,
        resolution=resolution,
        first_frame_image=first_frame_image,
        aspect_ratio=aspect_ratio,
        identity_reference_path=identity_reference_path,
        identity_snapshot=identity,
        asset_generation_id=state.generation_id,
        channel_source=channel_source,
        identity_reference=(
            await asyncio.to_thread(load_avatar_bytes_as_data_uri, identity_reference_path)
            if identity_reference_path
            else None
        ),
    )
    job = VideoGenJob(
        user_id=user_id,
        session_id=session_id,
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
    await db.commit()
    try:
        submitted = await _submit_next_video(job.id, user_id)
    except BaseException:
        # 行已提交：提交被打断（含调用方取消）时仍由后台任务按落库状态收尾；submitting 按结果未知处理，不重发。
        _spawn_poll(job.id, user_id)
        raise
    if submitted:
        _spawn_poll(job.id, user_id)
    await db.refresh(job)
    return job


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
        if row is None or row.status in _TERMINAL_STATUSES:
            return
        row.status = "result_unknown" if reason in _RESULT_UNKNOWN_REASONS else "failed"
        row.error_reason = reason
        row.error_message = user_msg
        discarded = (row.candidate_video_url, row.video_url)
        row.candidate_video_url = row.video_url = None
        session_id = row.session_id
        if row.structured_reply:
            await update_video_reply(db, row)
        elif session_id:
            status_message: Message | None = None
            with contextlib.suppress(TypeError, ValueError):
                conversation = await db.get(Conversation, int(session_id))
                if conversation is not None and conversation.user_id == row.user_id:
                    status_message = Message(
                        conversation_id=conversation.id,
                        role="system",
                        subtype=MEDIA_FAILURE_SUBTYPE,
                        content=f"[视频任务 {job_id} 未完成] {user_msg}",
                    )
                    db.add(status_message)
                    await db.flush()
            emit_ws_event(
                db,
                user_id=row.user_id,
                event_type="video_gen.failed",
                payload={
                    "task_id": str(job_id),
                    "error": user_msg,
                    "session_id": session_id,
                    "status_message_id": status_message.id if status_message is not None else None,
                    "status_text": status_message.content if status_message is not None else None,
                },
            )
            await _add_channel_delivery(db, row, text=f"视频生成失败（任务 {job_id}）：{user_msg}", media=[])
        await db.commit()
    for path in discarded:
        await asyncio.to_thread(unlink_companion_asset, path)


async def _finalize_best_video(job_id: int, *, warning: str | None = None) -> None:
    """保存最佳资产与终态；聊天交付同事务提交，重复恢复不再送达。"""
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or row.status in _TERMINAL_STATUSES:
            return
        snapshot = _VideoJobParams.model_validate_json(row.params_json).identity_snapshot
        if snapshot is not None and not await character_snapshot_is_current(db, row.user_id, snapshot):
            await db.rollback()
            await _record_failure(job_id, reason="identity_changed")
            return
        storage_url = row.video_url
        if not storage_url or _stored_asset_file(row.user_id, storage_url) is None:
            raise RuntimeError("best video asset is unavailable")
        client_url = client_asset_url(storage_url)
        media = [{"type": "video", "url": storage_url}]
        client_media = [{"type": "video", "url": client_url}]
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
        session_id = row.session_id
        if row.structured_reply:
            await update_video_reply(db, row)
            await db.commit()
            return
        if not session_id:
            await db.commit()
            return
        with contextlib.suppress(TypeError, ValueError):
            conversation = await db.get(Conversation, int(session_id))
            if conversation is not None and conversation.user_id == row.user_id:
                db.add(
                    Message(
                        conversation_id=conversation.id,
                        role="system",
                        subtype=MEDIA_STATUS_SUBTYPE,
                        content=f"[视频已生成 task {job_id}] {client_url}",
                        media_json=json.dumps(media, ensure_ascii=False),
                    ),
                )
        emit_ws_event(
            db,
            user_id=row.user_id,
            event_type="video_gen.completed",
            payload={
                "task_id": str(job_id),
                "url": client_url,
                "media": client_media,
                **({"session_id": session_id} if session_id else {}),
                **({"warning": warning} if warning else {}),
            },
        )
        await _add_channel_delivery(db, row, text=f"视频已生成（任务 {job_id}）", media=media)
        await db.commit()


async def _evaluate_stored_video(job_id: int) -> str:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or row.status != "evaluating" or not row.candidate_video_url:
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
            identity_uri=params.identity_reference,
            identity_text=render_character_identity(snapshot) if snapshot else "",
            channel_source=params.channel_source,
        )
    except GenerationWorkPaused:
        return "paused"
    except GenerationAuthorizationRevoked:
        return "revoked"
    if snapshot is not None:
        async with SESSION_LOCAL() as db:
            if not await character_snapshot_is_current(db, user_id, snapshot):
                return "stale"
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or row.status != "evaluating" or row.candidate_video_url != candidate:
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
            row.generation_state_json = state.model_dump_json()
            await db.commit()
            await asyncio.to_thread(unlink_companion_asset, candidate)
            return "retry"
        entry = MediaCandidate(path=candidate, attempt=row.generation_attempt_index)
        state.candidates.append(entry)
        state.accept_score(entry, score)
        best = state.best()
        row.video_url = best.path
        row.candidate_video_url = None
        state.phase = "ready"
        decision = "retry" if state.needs_next() else "complete"
        if decision == "retry":
            row.status = "retry_pending"
        row.generation_state_json = state.model_dump_json()
        await db.commit()
    for entry in state.candidates:
        if entry.path != best.path:
            await asyncio.to_thread(unlink_companion_asset, entry.path)
    return decision


def _no_submission_reason(stop_reason: str, submit_failed: bool) -> str:
    """没有可提交的供应商时的失败原因：此前提交失败的如实报告，结果未知不当作普通失败；只有供应商配置失效才报服务配置变更。"""
    if stop_reason == "result_unknown":
        return "submit_result_unknown"
    if stop_reason == "content_policy_blocked":
        return stop_reason
    return "submit_failed" if stop_reason or submit_failed else "provider_unavailable"


async def _submit_next_video(job_id: int, user_id: int) -> bool:
    submit_failed = False
    while True:
        if is_user_in_maintenance(user_id):
            return False
        async with SESSION_LOCAL() as db:
            row = await db.get(VideoGenJob, job_id)
            if row is None or row.status != "retry_pending":
                return False
            state = MediaChainState.model_validate_json(row.generation_state_json)
            params = _VideoJobParams.model_validate_json(row.params_json)
            prompt = row.prompt
        if state.stop_reason or state.next_index >= len(state.providers):
            await _fail_or_keep_best(job_id, _no_submission_reason(state.stop_reason, submit_failed))
            return False
        index = state.next_index
        config = await resolve_frozen_media_provider(user_id, "video_gen", state.providers[index])
        if config is None:
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or current.status != "retry_pending":
                    return False
                if MediaChainState.model_validate_json(current.generation_state_json).next_index != index:
                    return False
                state.next_index += 1
                current.generation_state_json = state.model_dump_json()
                await db.commit()
            continue
        state.begin(index)
        async with SESSION_LOCAL() as db:
            current = await db.get(VideoGenJob, job_id, with_for_update=True)
            if current is None or current.status != "retry_pending":
                return False
            if MediaChainState.model_validate_json(current.generation_state_json).next_index != index:
                return False
            if is_user_in_maintenance(user_id):
                return False
            current.status = "submitting"
            current.generation_state_json = state.model_dump_json()
            current.generation_attempt_index = index
            current.provider = config.provider_name
            current.model = config.model
            current.provider_task_id = None
            await db.commit()
        request = VideoGenRequest(
            prompt=prompt,
            duration=params.duration,
            resolution=params.resolution,
            first_frame_image=params.first_frame_image,
            aspect_ratio=params.aspect_ratio,
        )

        async def submit(provider: VideoGenProvider) -> VideoJobStatus:
            await require_video_generation_call(user_id, params.channel_source)
            result = await provider.submit(request)
            if not result.task_id:
                raise ProviderResultUnknownError("POST", config.base_url)
            return result

        try:
            submitted = await execute_with_fallback([config], VideoGenProvider, submit, user_id=user_id)
        except GenerationAuthorizationRevoked:
            await _fail_or_keep_best(job_id, "authorization_revoked")
            return False
        except GenerationWorkPaused:
            state.phase = "ready"
            state.next_index = index
            state.active_index = None
            await _update_job(job_id, status="retry_pending", generation_state_json=state.model_dump_json())
            return False
        except Exception as exc:
            reason, can_continue = media_failure_reason(exc)
            classified = classify_api_error(exc)
            logger.warning(
                "video submit failed",
                extra={
                    "job_id": job_id,
                    "provider": config.provider_name,
                    "model": config.model,
                    "chain_index": index,
                    "reason": reason,
                    "can_continue": can_continue,
                    "status_code": classified.status_code,
                    "error": classified.message,
                },
            )
            state.phase = "ready"
            if not can_continue or state.next_index >= len(state.providers):
                state.stop_reason = reason
            await _update_job(job_id, status="retry_pending", generation_state_json=state.model_dump_json())
            submit_failed = True
            continue
        state.phase = "processing"
        await _update_job(
            job_id,
            status="processing",
            provider_task_id=submitted.task_id,
            generation_state_json=state.model_dump_json(),
        )
        return True


async def _pinned_video_provider(user_id: int, state: MediaChainState) -> VideoGenProvider | None:
    """当前尝试所绑定的供应商；配置或端点已变更时返回 None。"""
    if state.active_index is None:
        return None
    config = await resolve_frozen_media_provider(user_id, "video_gen", state.providers[state.active_index])
    return build_provider(config, VideoGenProvider) if config is not None else None


async def _fail_or_keep_best(job_id: int, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
    if row is not None and row.video_url:
        state = MediaChainState.model_validate_json(row.generation_state_json)
        state.stop_reason = state.stop_reason or reason
        await _update_job(job_id, generation_state_json=state.model_dump_json(), candidate_video_url=None)
        await asyncio.to_thread(unlink_companion_asset, row.candidate_video_url)
        warning = (
            f"{video_failure_message(reason)}；已保留此前最佳视频"
            if reason in {"result_unknown", "submit_result_unknown", "content_policy_blocked", "timeout"}
            else "自动重生成未能完成，已保留此前最佳视频"
        )
        await _finalize_best_video(job_id, warning=warning)
    else:
        await _record_failure(job_id, reason=reason)


# 进程内 in-flight 集合：并发 finalize 同一任务时只有第一个进入，避免重复下载/发事件；重启后由 resume_pending_video_jobs 按 DB 重建。
async def _poll_and_finalize(job_id: int) -> None:
    """后台主循环：供应商任务和本地候选均可恢复；仅未知提交禁止自动重发。"""
    if job_id in _INFLIGHT:
        return
    _INFLIGHT.add(job_id)
    try:
        await _poll_and_finalize_locked(job_id)
    finally:
        _INFLIGHT.discard(job_id)


async def _poll_and_finalize_locked(job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
        if job is None:
            return
        # 幂等护栏：上一次已终结的行不再重复下载/发事件；``_update_job`` 内部的终态检查是双保险。
        if job.status in _TERMINAL_STATUSES:
            logger.info("skipping already-finalized job", extra={"job_id": job_id, "status": job.status})
            return
        user_id = job.user_id
        provider_task_id = job.provider_task_id or ""

    try:
        if job.status == "submitting":
            state = MediaChainState.model_validate_json(job.generation_state_json)
            state.stop_reason = "result_unknown"
            await _update_job(job_id, generation_state_json=state.model_dump_json())
            await _fail_or_keep_best(job_id, "submit_result_unknown")
            return
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
                    generation_id=_VideoJobParams.model_validate_json(job.params_json).asset_generation_id,
                )
                if _stored_asset_file(user_id, candidate_path) is None:
                    candidate_path = None
            if candidate_path:
                await _update_job(job_id, status="evaluating", candidate_video_url=candidate_path)
                decision = await _evaluate_stored_video(job_id)
                if decision == "paused":
                    return
                if decision == "revoked":
                    await _record_failure(job_id, reason="authorization_revoked")
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
                    generation_id=_VideoJobParams.model_validate_json(job.params_json).asset_generation_id,
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

        provider = await _pinned_video_provider(user_id, state)
        if provider is None:
            await _fail_or_keep_best(job_id, "provider_unavailable")
            return

        async def ensure_pending() -> None:
            # 感知并发终态（如行被删除、其他 worker 已终结）后停止轮询。
            async with SESSION_LOCAL() as db:
                row = await db.get(VideoGenJob, job_id)
            if row is None or row.status in _TERMINAL_STATUSES:
                raise _JobSettledError

        try:
            status = await poll_video_task(provider, provider_task_id, before_poll=ensure_pending)
        except _JobSettledError:
            return
        except VideoPollTimeoutError:
            await _fail_or_keep_best(job_id, "timeout")
            return

        if status.status == "succeeded":
            state.phase = "storing"
            state.result_url = status.download_url
            if not state.result_url:
                await _fail_or_keep_best(job_id, "download_failed")
                return
            if job.status != "downloading":
                async with SESSION_LOCAL() as db:
                    claimed = (
                        await db.execute(
                            update(VideoGenJob)
                            .where(VideoGenJob.id == job_id, VideoGenJob.status == "processing")
                            .values(status="downloading", generation_state_json=state.model_dump_json()),
                        )
                    ).rowcount
                    await db.commit()
                if not claimed:
                    return
            else:
                await _update_job(job_id, generation_state_json=state.model_dump_json())
            await _poll_and_finalize_locked(job_id)
            return
        if state.needs_next():
            state.phase = "ready"
            await _update_job(job_id, status="retry_pending", generation_state_json=state.model_dump_json())
            if await _submit_next_video(job_id, user_id):
                await _poll_and_finalize_locked(job_id)
            return
        await _fail_or_keep_best(job_id, video_provider_failure_reason(status.error))
    except Exception:
        logger.exception("unhandled exception in video poll worker", extra={"job_id": job_id})
        try:
            async with SESSION_LOCAL() as db:
                latest = await db.get(VideoGenJob, job_id)
            if latest is not None and latest.status in ("downloading", "evaluating"):
                candidate = latest.candidate_video_url or video_job_asset_path(
                    user_id,
                    job_id,
                    latest.generation_attempt_index,
                    generation_id=_VideoJobParams.model_validate_json(latest.params_json).asset_generation_id,
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
    generation_id: str | None,
) -> str:
    """有界重试下载供应商成品并原子写入确定性路径，返回裸存储路径。只对传输错误与服务端 5xx 退避重试，超限、4xx 与过期地址等确定性失败直接上抛。慢速大文件读取超时放宽到 10 分钟，上限 ``video_gen_download_max_bytes``。"""
    if not download_url:
        raise RuntimeError("video result has no download url")
    data = await download_media_result(download_url, max_bytes=SETTINGS.video_gen_download_max_bytes, timeout=600.0)
    if sniff_media_ext(data) != "mp4":
        raise RuntimeError("provider returned a payload that is not an mp4 stream")
    return await save_video_job_asset_async(
        data,
        user_id=user_id,
        job_id=job_id,
        attempt=attempt,
        generation_id=generation_id,
    )


async def resume_pending_video_jobs() -> None:
    """从供应商句柄或确定性本地路径恢复，绝不重复提交结果未知的付费任务。"""

    async with SESSION_LOCAL() as db:
        jobs = (
            await db.execute(
                select(VideoGenJob.id, VideoGenJob.user_id).where(
                    VideoGenJob.status.in_(
                        ("processing", "downloading", "evaluating", "retry_pending", "submitting"),
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
                        VideoGenJob.status.in_(("processing", "downloading", "evaluating", "retry_pending")),
                    ),
                )
            ).all(),
        )
    for job_id in job_ids:
        _spawn_poll(job_id, user_id)


def _result_lock(job_id: int) -> asyncio.Lock:
    return _RESULT_LOCKS.setdefault(job_id, asyncio.Lock())


def post_video_ready(job: VideoGenJob) -> bool:
    path = job.video_url or job.candidate_video_url
    if job.id in _INFLIGHT or not path or _stored_asset_file(job.user_id, path) is None:
        return False
    if job.status == "succeeded":
        return True
    state = MediaChainState.model_validate_json(job.generation_state_json)
    return job.status not in ("failed", "discarded") and state.source_path == path


async def query_post_video_job(user_id: int, job_id: int) -> VideoGenJob:
    """用户显式查询原句柄或下载原成品；不提交生成、不推进供应商链、不调用模型评分。"""
    async with _result_lock(job_id):
        async with SESSION_LOCAL() as db:
            row = await get_job(db, job_id, user_id)
        if row is None or row.session_id is not None:
            raise ValueError("找不到动态视频任务")
        if row.status in ("succeeded", "failed", "discarded") or job_id in _INFLIGHT:
            return row
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
            )
        )
        local = _stored_asset_file(user_id, candidate)
        if local is not None and candidate:
            await asyncio.to_thread(probe_video, local)
            async with SESSION_LOCAL() as db:
                current = await db.get(VideoGenJob, job_id, with_for_update=True)
                if current is None or current.status == "discarded":
                    raise ValueError("视频任务已放弃")
                state.source_path = candidate
                current.candidate_video_url = candidate
                current.status = "review_pending"
                current.generation_state_json = state.model_dump_json()
                await db.commit()
                return current
        result_url = state.result_url
        if not result_url and row.provider_task_id:
            provider = await _pinned_video_provider(user_id, state)
            if provider is None:
                raise ValueError("原视频服务配置已变更，暂不能查询")
            async with asyncio.timeout(120):
                result = await provider.poll(row.provider_task_id)
            if result.status == "failed":
                async with SESSION_LOCAL() as db:
                    current = await db.get(VideoGenJob, job_id, with_for_update=True)
                    if current is not None and current.user_id == user_id and current.status != "discarded":
                        current.status = "failed"
                        current.error_reason = video_provider_failure_reason(result.error)
                        current.error_message = video_failure_message(current.error_reason)
                        await db.commit()
                        return current
            if result.status != "succeeded":
                return row
            result_url = result.download_url
        if not result_url:
            return row
        stored = await _download_and_store(
            result_url,
            user_id=user_id,
            job_id=job_id,
            attempt=row.generation_attempt_index,
            generation_id=params.asset_generation_id,
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
            return current


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


async def discard_post_video_job(user_id: int, job_id: int, publication_id: str) -> None:
    """停止原任务并清理它独占的成品；保留外部任务句柄，不声称远端操作已撤销。"""
    async with _result_lock(job_id):
        async with SESSION_LOCAL() as db:
            initial = await get_job(db, job_id, user_id)
            if initial is None:
                return
            if initial.session_id is not None or initial.reply_message_id is not None:
                raise ValueError("该视频任务仍用于会话，不能作为动态成品放弃")
            shared = await db.scalar(
                select(PostPublication.id)
                .where(
                    PostPublication.id != publication_id,
                    PostPublication.status != "discarded",
                    PostPublication.progress_json["job_id"].as_string() == str(job_id),
                )
                .limit(1),
            )
            if shared is not None:
                return
        task = _POLL_TASKS.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id).with_for_update(),
            )
            if row is None:
                return
            if row.session_id is not None or row.reply_message_id is not None:
                raise ValueError("该视频任务仍用于会话，不能作为动态成品放弃")
            state = MediaChainState.model_validate_json(row.generation_state_json)
            params = _VideoJobParams.model_validate_json(row.params_json)
            paths = {
                path
                for path in (
                    row.video_url,
                    row.candidate_video_url,
                    state.source_path,
                    *(entry.path for entry in state.candidates),
                )
                if isinstance(path, str) and path
            }
            owned_paths = {
                video_job_asset_path(user_id, job_id, entry.attempt, generation_id=params.asset_generation_id)
                for entry in state.candidates
            }
            owned_paths.add(
                video_job_asset_path(
                    user_id,
                    job_id,
                    row.generation_attempt_index,
                    generation_id=params.asset_generation_id,
                ),
            )
            paths.update(path for path in owned_paths if _stored_asset_file(user_id, path) is not None)
            discarded: list[str] = []
            for path in paths & owned_paths:
                referenced = await db.scalar(
                    select(CompanionPost.id)
                    .where(or_(CompanionPost.media_url == path, CompanionPost.audio_url == path))
                    .limit(1),
                )
                referenced = referenced or await db.scalar(
                    select(PostPublication.id)
                    .where(
                        PostPublication.id != publication_id,
                        PostPublication.status != "discarded",
                        or_(
                            PostPublication.progress_json["job_id"].as_string() == str(job_id),
                            PostPublication.progress_json["media_url"].as_string() == path,
                        ),
                    )
                    .limit(1),
                )
                referenced = referenced or await db.scalar(
                    select(Message.id)
                    .where(
                        or_(
                            Message.media_json.contains(path, autoescape=True),
                            Message.reply_json.contains(path, autoescape=True),
                            Message.content.contains(path, autoescape=True),
                        ),
                    )
                    .limit(1),
                )
                referenced = referenced or await db.scalar(
                    select(CompanionAction.id)
                    .where(CompanionAction.result_json.contains(path, autoescape=True))
                    .limit(1),
                )
                referenced = referenced or await db.scalar(
                    select(VideoGenJob.id)
                    .where(
                        VideoGenJob.id != job_id,
                        or_(VideoGenJob.video_url == path, VideoGenJob.candidate_video_url == path),
                    )
                    .limit(1),
                )
                if referenced is None:
                    discarded.append(path)
            row.status = "discarded"
            row.error_reason = "user_discarded"
            row.error_message = "视频已放弃；远端任务可能仍在运行，未重新提交"
            state.stop_reason = "user_discarded"
            state.phase = "complete"
            row.generation_state_json = state.model_dump_json()
            await db.commit()
        for path in discarded:
            await asyncio.to_thread(unlink_companion_asset, path)
        async with SESSION_LOCAL() as db:
            row = await db.get(VideoGenJob, job_id, with_for_update=True)
            if row is None or row.status != "discarded":
                return
            state = MediaChainState.model_validate_json(row.generation_state_json)
            if row.video_url in discarded:
                row.video_url = None
            if row.candidate_video_url in discarded:
                row.candidate_video_url = None
            if state.source_path in discarded:
                state.source_path = None
            state.candidates = [entry for entry in state.candidates if entry.path not in discarded]
            row.generation_state_json = state.model_dump_json()
            await db.commit()
