import asyncio
import contextlib
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from components import (
    SESSION_LOCAL,
    SETTINGS,
    TaskBag,
    backoff_for_poll,
    download_capped,
    get_logger,
    safe_json_loads,
    track_user_task,
    utc_now,
)
from modules.channels import ChannelBinding, ChannelDelivery, ChannelDeliveryPayload
from modules.companion import AvatarAsset, CharacterCardSnapshot
from modules.conversation import Conversation, Message
from modules.media import VideoGenJob
from modules.ws import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import character_snapshot_is_current, render_character_identity
from services.domains.conversation import MEDIA_STATUS_SUBTYPE
from services.infrastructure.assets import (
    asset_store,
    build_data_uri,
    client_asset_url,
    save_video_job_asset_async,
    sniff_media_ext,
    unlink_companion_asset,
    video_job_asset_path,
)
from services.infrastructure.llm import (
    MissingLlmConfigError,
    ProviderResultUnknownError,
    ServiceType,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
    execute_with_fallback,
    resolve,
    resolve_provider_chain,
)
from services.infrastructure.video_processing import extract_cover, probe_video

from .avatar_service import load_avatar_bytes_as_data_uri
from .identity_review import score_character_frames
from .media_chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
)

logger = get_logger(__name__)

_INFLIGHT: set[int] = set()

_BG = TaskBag("media.video_jobs")
_TERMINAL_STATUSES = ("succeeded", "failed", "result_unknown")
_RESULT_UNKNOWN_MESSAGE = "视频提交结果不确定，供应商可能已接单；为避免重复计费，系统没有自动重试"
_DOWNLOAD_ATTEMPTS = 3


def video_generation_wait_seconds(job: VideoGenJob) -> float:
    state = MediaChainState.model_validate_json(job.generation_state_json)
    per_provider = (
        SETTINGS.video_gen_max_poll_seconds + _DOWNLOAD_ATTEMPTS * 600 + 2 * SETTINGS.llm_request_timeout_seconds
    )
    return max(1, len(state.providers)) * per_provider + 90


async def _score_self_video(
    user_id: int,
    video_url: str,
    identity_path: str | None,
    *,
    identity_uri: str | None = None,
    identity_text: str = "",
) -> tuple[str, int | None]:
    if not identity_path:
        return "unavailable", None

    async with SESSION_LOCAL() as db:
        current_seed = await db.scalar(
            select(AvatarAsset.seed_fullbody_url).where(
                AvatarAsset.user_id == user_id,
                AvatarAsset.active.is_(True),
            ),
        )
    if current_seed != identity_path:
        return "stale", None
    parsed = asset_store.parse_companion_asset_path(video_url)
    local = asset_store.resolve_companion_asset_path(*parsed) if parsed and parsed[0] == user_id else None
    if local is None:
        return "invalid", None
    try:
        probe = await asyncio.to_thread(probe_video, local[0])
    except Exception:
        logger.warning("chat video cannot be decoded", extra={"user_id": user_id}, exc_info=True)
        return "invalid", None
    try:
        identity_uri = identity_uri or await asyncio.to_thread(load_avatar_bytes_as_data_uri, identity_path)
    except Exception:
        logger.warning("chat video identity reference unavailable", extra={"user_id": user_id}, exc_info=True)
        return "unavailable", None
    try:
        canvas_w = max(2, min(probe.width, 1024) // 2 * 2)
        canvas_h = max(2, min(probe.height, 1024) // 2 * 2)
        frames: list[str] = []
        with tempfile.TemporaryDirectory(prefix="spiritagent-video-review-") as directory:
            for index, second in enumerate((0.0, probe.duration_seconds / 2, max(0.0, probe.duration_seconds - 0.15))):
                frame = Path(directory) / f"frame-{index}.webp"
                await asyncio.to_thread(
                    extract_cover,
                    local[0],
                    frame,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    at_seconds=second,
                )
                data = await asyncio.to_thread(frame.read_bytes)
                frames.append(build_data_uri(data, "image/webp"))
        score = await score_character_frames(user_id, identity_uri, tuple(frames), identity_text=identity_text)
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
    except Exception:
        logger.warning("chat video identity scoring failed", extra={"user_id": user_id}, exc_info=True)
        return "invalid", None


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
    session_id: str | None,
    *,
    text: str,
    media: list[dict[str, str]],
) -> None:
    if not session_id:
        return
    try:
        conv_id = int(session_id)
    except (TypeError, ValueError):
        return
    binding = (
        await db.execute(select(ChannelBinding).where(ChannelBinding.conversation_id == conv_id))
    ).scalar_one_or_none()
    if binding is None:
        return
    db.add(
        ChannelDelivery(
            binding_id=binding.id,
            peer_id="",
            payload_json=ChannelDeliveryPayload.model_validate({"text": text, "media": media}).model_dump_json(),
        ),
    )


async def get_job(db: AsyncSession, job_id: int, user_id: int) -> VideoGenJob | None:
    """按 user_id 过滤，避免 GET 接口泄露其他用户的任务。"""

    stmt = select(VideoGenJob).where(VideoGenJob.id == job_id, VideoGenJob.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


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
) -> "VideoGenJob":
    """冻结能力链并提交首个任务；轮询绑定实际接单供应商，低分才推进链尾。"""

    params = {
        "duration": duration,
        "resolution": resolution,
        "first_frame_image": first_frame_image,
        "aspect_ratio": aspect_ratio,
        "identity_reference_path": identity_reference_path,
    }

    chain = await resolve_provider_chain(db, user_id, "video_gen")
    compatible = []
    for config in chain:
        provider = resolve(ServiceType.video_gen, config.provider_name)(config)
        if first_frame_image and not provider.supports_first_frame:
            continue
        if provider.durations is not None and duration not in provider.durations:
            continue
        if provider.resolutions is not None and resolution.lower() not in {
            value.lower() for value in provider.resolutions
        }:
            continue
        compatible.append(config)
    if not compatible:
        raise MissingLlmConfigError("未配置支持本次首帧、时长和分辨率的视频供应商")
    state = MediaChainState(providers=[FrozenMediaProvider.from_config(config) for config in compatible])
    params["identity_snapshot"] = identity.model_dump() if identity else None
    params["identity_reference"] = (
        await asyncio.to_thread(load_avatar_bytes_as_data_uri, identity_reference_path)
        if identity_reference_path
        else None
    )
    job = VideoGenJob(
        user_id=user_id,
        session_id=session_id,
        provider=compatible[0].provider_name,
        model=compatible[0].model,
        prompt=prompt,
        params_json=json.dumps(params),
        status="retry_pending",
        generation_state_json=state.model_dump_json(),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await db.commit()
    if not await _submit_next_video(job.id, user_id):
        await db.refresh(job)
        return job
    await db.refresh(job)
    await db.commit()

    t = asyncio.create_task(_poll_and_finalize(job.id))
    _BG.add(t, on_error=_on_video_task_error)
    track_user_task(user_id, t, cancel_on_maintenance=False)
    return job


_FAILURE_COPY: dict[str, str] = {
    "submit_result_unknown": _RESULT_UNKNOWN_MESSAGE,
    "submit_failed": "视频提交失败，请稍后重试",
    "missing_task_id": "视频服务暂不可用，请稍后重试",
    "provider_unavailable": "视频 provider 配置变更，请稍后重试",
    "provider_failed": "视频生成失败，请稍后重试",
    "download_failed": "视频下载失败，请稍后重试",
    "download_interrupted": "视频下载中断，请重新生成",
    "timeout": "视频生成超时，请稍后重试",
    "poll_failed": "视频生成失败，请稍后重试",
    "worker_failed": "视频生成服务异常，请稍后重试",
    "identity_changed": "角色外形已更新，旧参考生成的视频未交付",
    "quality_failed": "视频文件无法完成质量核查，请稍后重试",
}

_POLICY_KEYWORDS = ("policy", "unsafe", "content_filter", "敏感", "违规", "moderation")


def _failure_user_message(reason: str, exc: BaseException | None) -> str:
    """按失败原因挑选预设文案；策略审核相关的异常文本仅做关键词嗅探，原始异常文本绝不流到渲染端（ARCH §11#2）。"""
    msg = _FAILURE_COPY.get(reason, "视频生成失败，请稍后重试")
    if exc is not None:
        text = str(exc).lower()
        if any(k in text for k in _POLICY_KEYWORDS):
            return "内容审核未通过，请调整提示词后重试"
    return msg


async def _record_failure(
    job_id: int,
    *,
    reason: str,
    exc: BaseException | None = None,
    exc_text: str | None = None,
) -> None:
    """写入脱敏后的失败行与对应 WSEvent；``exc`` 仅服务端记录，``error_message`` 与 WS 事件载荷只携带预设文案——原始供应商文本与内部字符串绝不外泄。"""
    if exc is not None:
        logger.exception("video job failure", extra={"job_id": job_id, "reason": reason})
    elif exc_text is not None:
        logger.warning("video job failure", extra={"job_id": job_id, "reason": reason, "raw": exc_text[:200]})
    else:
        logger.warning("video job failure", extra={"job_id": job_id, "reason": reason})
    sniff_exc: BaseException | None = exc if exc is not None else (RuntimeError(exc_text) if exc_text else None)
    user_msg = _failure_user_message(reason, sniff_exc)
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or row.status in _TERMINAL_STATUSES:
            return
        row.status = "result_unknown" if reason == "submit_result_unknown" else "failed"
        row.error_reason = reason
        row.error_message = user_msg
        session_id = row.session_id
        emit_ws_event(
            db,
            user_id=row.user_id,
            event_type="video_gen.failed",
            payload={"task_id": str(job_id), "error": user_msg, **({"session_id": session_id} if session_id else {})},
        )
        await _add_channel_delivery(db, session_id, text=f"视频生成失败（任务 {job_id}）：{user_msg}", media=[])
        await db.commit()


async def _finalize_best_video(job_id: int, *, warning: str | None = None) -> None:
    """已知最佳资产、任务终态和 WS outbox 同事务提交；重复恢复不会重复交付。"""
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or row.status in _TERMINAL_STATUSES:
            return
        params = safe_json_loads(row.params_json, default={})
        identity_current = True
        if params.get("identity_snapshot"):
            identity_current = await character_snapshot_is_current(
                db,
                row.user_id,
                CharacterCardSnapshot.model_validate(params["identity_snapshot"]),
            )
        elif params.get("identity_reference_path"):
            seed = await db.scalar(
                select(AvatarAsset.seed_fullbody_url)
                .where(AvatarAsset.user_id == row.user_id, AvatarAsset.active.is_(True))
                .with_for_update(),
            )
            identity_current = seed == params["identity_reference_path"]
        if not identity_current:
            await db.rollback()
            await _record_failure(job_id, reason="identity_changed")
            return
        storage_url = row.video_url
        parsed = asset_store.parse_companion_asset_path(storage_url) if storage_url else None
        if parsed is None or parsed[0] != row.user_id or asset_store.resolve_companion_asset_path(*parsed) is None:
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
        row.candidate_file_id = None
        row.error_reason = state.stop_reason if warning else None
        row.error_message = warning
        session_id = row.session_id
        if session_id:
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
        await _add_channel_delivery(db, session_id, text=f"视频已生成（任务 {job_id}）", media=media)
        await db.commit()


async def _evaluate_stored_video(job_id: int) -> str:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or row.status != "evaluating" or not row.candidate_video_url:
            return "invalid"
        candidate = row.candidate_video_url
        params = safe_json_loads(row.params_json or "{}", default={})
        user_id = row.user_id
    snapshot = (
        CharacterCardSnapshot.model_validate(params["identity_snapshot"]) if params.get("identity_snapshot") else None
    )
    if snapshot is not None:
        async with SESSION_LOCAL() as db:
            if not await character_snapshot_is_current(db, user_id, snapshot):
                return "stale"
    outcome, score = await _score_self_video(
        user_id,
        candidate,
        params.get("identity_reference_path"),
        identity_uri=params.get("identity_reference"),
        identity_text=render_character_identity(snapshot) if snapshot else "",
    )
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
        entry = MediaCandidate(path=candidate, attempt=row.generation_attempt_index)
        state.candidates.append(entry)
        state.accept_score(entry, score)
        best = state.best()
        row.video_url = best.path
        row.file_id = best.path.rsplit("/", 1)[-1]
        row.candidate_video_url = None
        row.candidate_file_id = None
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


async def _submit_next_video(job_id: int, user_id: int) -> bool:
    while True:
        async with SESSION_LOCAL() as db:
            row = await db.get(VideoGenJob, job_id)
            if row is None or row.status != "retry_pending":
                return False
            state = MediaChainState.model_validate_json(row.generation_state_json)
            params = safe_json_loads(row.params_json or "{}", default={})
            prompt = row.prompt
        if state.stop_reason or state.next_index >= len(state.providers):
            await _fail_or_keep_best(job_id, "provider_unavailable")
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
            current.status = "submitting"
            current.generation_state_json = state.model_dump_json()
            current.generation_attempt_index = index
            current.provider = config.provider_name
            current.model = config.model
            current.provider_task_id = None
            current.provider_file_id = None
            await db.commit()
        request = VideoGenRequest(
            prompt=prompt,
            duration=params.get("duration"),
            resolution=params.get("resolution"),
            first_frame_image=params.get("first_frame_image"),
            aspect_ratio=params.get("aspect_ratio"),
        )

        async def submit(provider: VideoGenProvider) -> VideoJobStatus:
            result = await provider.submit(request)
            if not result.task_id:
                raise ProviderResultUnknownError("POST", config.base_url)
            return result

        try:
            submitted = await execute_with_fallback(None, user_id, "video_gen", call_fn=submit, _chain=[config])
        except Exception as exc:
            reason, can_continue = media_failure_reason(exc)
            state.phase = "ready"
            if not can_continue:
                state.stop_reason = reason
            await _update_job(job_id, status="retry_pending", generation_state_json=state.model_dump_json())
            if can_continue:
                continue
            await _fail_or_keep_best(
                job_id,
                "submit_result_unknown" if reason == "result_unknown" else "submit_failed",
            )
            return False
        state.phase = "processing"
        await _update_job(
            job_id,
            status="processing",
            provider_task_id=submitted.task_id,
            generation_state_json=state.model_dump_json(),
        )
        return True


async def _pinned_video_provider(job_id: int, user_id: int) -> VideoGenProvider | None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None:
            return None
        state = MediaChainState.model_validate_json(row.generation_state_json)
    if state.active_index is None:
        return None
    config = await resolve_frozen_media_provider(user_id, "video_gen", state.providers[state.active_index])
    return resolve(ServiceType.video_gen, config.provider_name)(config) if config is not None else None


async def _fail_or_keep_best(job_id: int, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        has_best = bool(row and row.video_url)
    if has_best:
        state = MediaChainState.model_validate_json(row.generation_state_json)
        state.stop_reason = state.stop_reason or reason
        await _update_job(job_id, generation_state_json=state.model_dump_json())
        await _finalize_best_video(job_id, warning="自动重生成未能完成，已保留此前最佳视频")
    else:
        await _record_failure(job_id, reason=reason)


# In-flight 集合：进程中途重启时，多个协程可能竞争 finalize 同一任务。第一个进入的注册，后续提前退出，避免重复下载或重复 WSEvent；集合驻留在进程内存（重启即丢失——重启后由 resume_pending_video_jobs 走 DB 重建）。
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
                candidate_path = video_job_asset_path(user_id, job_id, job.generation_attempt_index)
                parsed_candidate = asset_store.parse_companion_asset_path(candidate_path)
                if parsed_candidate is None or asset_store.resolve_companion_asset_path(*parsed_candidate) is None:
                    candidate_path = None
            if candidate_path:
                await _update_job(
                    job_id,
                    status="evaluating",
                    candidate_video_url=candidate_path,
                    candidate_file_id=candidate_path.rsplit("/", 1)[-1],
                )
                decision = await _evaluate_stored_video(job_id)
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
        if job.status == "downloading" and (state.result_url or state.result_file_id):
            provider = None if state.result_url else await _pinned_video_provider(job_id, user_id)
            try:
                if not state.result_url and provider is not None:
                    state.result_url = (await provider.fetch(state.result_file_id)).download_url
                    await _update_job(job_id, generation_state_json=state.model_dump_json())
                file_id, storage_url = await _download_and_store(
                    provider,
                    state.result_file_id,
                    download_url=state.result_url,
                    user_id=user_id,
                    job_id=job_id,
                    attempt=job.generation_attempt_index,
                )
            except Exception:
                logger.warning("known video result awaits download recovery", extra={"job_id": job_id}, exc_info=True)
                if job.video_url:
                    await _fail_or_keep_best(job_id, "download_failed")
                    return
                await _update_job(
                    job_id,
                    error_reason="download_failed",
                    error_message=_FAILURE_COPY["download_failed"],
                )
                return
            await _update_job(
                job_id,
                status="evaluating",
                candidate_file_id=file_id,
                candidate_video_url=storage_url,
                error_reason=None,
                error_message=None,
            )
            await _poll_and_finalize_locked(job_id)
            return
        if job.status == "evaluating" and job.video_url:
            await _finalize_best_video(job_id)
            return
        if not provider_task_id:
            # 提交完成但 task_id 未持久化（极小概率，但保持防御），快速失败并给出明确原因，避免行一直处于 limbo。
            await _record_failure(job_id, reason="missing_task_id")
            return

        provider = await _pinned_video_provider(job_id, user_id)
        if provider is None:
            await _fail_or_keep_best(job_id, "provider_unavailable")
            return

        interval = SETTINGS.video_gen_poll_interval_seconds
        backoff_max = SETTINGS.video_gen_poll_backoff_max_seconds
        deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
        attempt = 0
        last_status: str | None = None
        while True:
            remaining = max(0.0, (deadline - utc_now()).total_seconds())
            if remaining <= 0:
                await _fail_or_keep_best(job_id, "timeout")
                return
            # 重新加载行以感知并发终态更新（如用户 DELETE 行、其他 worker 已终结）。provider_task_id 为空表示行被中途清空。
            async with SESSION_LOCAL() as db:
                job = await db.get(VideoGenJob, job_id)
                if job is None or job.status in _TERMINAL_STATUSES:
                    return
                current_task_id = job.provider_task_id or provider_task_id

            try:
                status = await provider.poll(current_task_id)
            except Exception:
                logger.exception("video poll failed", extra={"job_id": job_id})
                await _fail_or_keep_best(job_id, "poll_failed")
                return

            if status.status == "succeeded":
                state = MediaChainState.model_validate_json(job.generation_state_json)
                state.phase = "storing"
                state.result_url = status.download_url
                state.result_file_id = status.file_id
                if not state.result_url and not state.result_file_id:
                    await _fail_or_keep_best(job_id, "download_failed")
                    return
                if job.status != "downloading":
                    async with SESSION_LOCAL() as db:
                        claimed = (
                            await db.execute(
                                update(VideoGenJob)
                                .where(VideoGenJob.id == job_id, VideoGenJob.status.in_(("queued", "processing")))
                                .values(
                                    status="downloading",
                                    provider_file_id=status.file_id,
                                    generation_state_json=state.model_dump_json(),
                                ),
                            )
                        ).rowcount
                        await db.commit()
                        if not claimed:
                            return
                else:
                    await _update_job(job_id, generation_state_json=state.model_dump_json())
                await _poll_and_finalize_locked(job_id)
                return
            if status.status == "failed":
                state = MediaChainState.model_validate_json(job.generation_state_json)
                if state.needs_next():
                    state.phase = "ready"
                    await _update_job(job_id, status="retry_pending", generation_state_json=state.model_dump_json())
                    if await _submit_next_video(job_id, user_id):
                        await _poll_and_finalize_locked(job_id)
                    return
                await _fail_or_keep_best(job_id, "provider_failed")
                return

            await _update_job(job_id, status="processing")
            # 状态转换时重置退避；同状态持续时递增退避间隔
            if last_status is not None and status.status != last_status:
                attempt = 0
            elif last_status is not None:
                attempt += 1
            last_status = status.status
            sleep_for = backoff_for_poll(
                attempt,
                base_interval=interval,
                max_interval=backoff_max,
                remaining_seconds=remaining,
            )
            if sleep_for <= 0:  # 兜底:剩余时间用尽,提前退出
                await _fail_or_keep_best(job_id, "timeout")
                return
            await asyncio.sleep(sleep_for)
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
                )
                parsed = asset_store.parse_companion_asset_path(candidate)
                if parsed is not None and asset_store.resolve_companion_asset_path(*parsed) is not None:
                    logger.warning("stored video awaits recovery", extra={"job_id": job_id, "path": candidate})
                    return
            await _fail_or_keep_best(job_id, "worker_failed")
        except Exception:
            logger.exception("could not update video job after worker error", extra={"job_id": job_id})


async def _download_and_store(
    provider: VideoGenProvider | None,
    file_id: str | None,
    *,
    download_url: str | None = None,
    user_id: int,
    job_id: int,
    attempt: int,
) -> tuple[str, str]:
    """从供应商下载视频字节并转存 ``companion-assets/{user_id}/`` 永久资产，返回 (文件名, 裸存储路径)。MiniMax-H3 v2 在成功路径直接返回 URL（填 ``download_url``），跳过额外 ``fetch()``；旧版 MiniMax-Hailuo v1 把 URL 藏在 ``files/retrieve`` 接口后（填 ``file_id``）。"""
    storage_path = video_job_asset_path(user_id, job_id, attempt)
    existing = asset_store.resolve_companion_asset_path(user_id, storage_path.rsplit("/", 1)[-1])
    if existing is not None:
        return storage_path.rsplit("/", 1)[-1], storage_path
    if not download_url:
        if not file_id or provider is None:
            raise RuntimeError("provider.poll succeeded without file_id or download_url")
        download_url = (await provider.fetch(file_id)).download_url
    for attempt_index in range(_DOWNLOAD_ATTEMPTS):
        try:
            data = await _stream_download(download_url)
            break
        except Exception:
            if attempt_index + 1 == _DOWNLOAD_ATTEMPTS:
                raise
    if sniff_media_ext(data) != "mp4":
        raise RuntimeError("provider returned a payload that is not an mp4 stream")
    storage_path = await save_video_job_asset_async(data, user_id=user_id, job_id=job_id, attempt=attempt)
    return storage_path.rsplit("/", 1)[-1], storage_path


async def _stream_download(url: str) -> bytes:
    """受限下载，上限为 ``video_gen_download_max_bytes``——LLM 默认 30–60s 读取超时对慢速 200MB 视频远不够，改用 10 分钟。"""
    cap = SETTINGS.video_gen_download_max_bytes
    return await download_capped(url, max_bytes=cap, timeout=600.0)


async def resume_pending_video_jobs() -> None:
    """从供应商句柄或确定性本地路径恢复，绝不重复提交结果未知的付费任务。"""

    async with SESSION_LOCAL() as db:
        rows = (
            (
                await db.execute(
                    select(VideoGenJob).where(
                        VideoGenJob.status.in_(
                            ("queued", "processing", "downloading", "evaluating", "retry_pending", "submitting"),
                        ),
                    ),
                )
            )
            .scalars()
            .all()
        )
        jobs = [(r.id, r.user_id) for r in rows]
    for job_id, user_id in jobs:
        t = asyncio.create_task(_poll_and_finalize(job_id))
        _BG.add(t, on_error=_on_video_task_error)
        track_user_task(user_id, t, cancel_on_maintenance=False)
    if jobs:
        logger.info("Resumed pending video jobs", extra={"count": len(jobs)})
