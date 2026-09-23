import asyncio
import contextlib
import json
import tempfile
from dataclasses import replace
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
from modules.companion import AvatarAsset
from modules.conversation import Conversation, Message
from modules.media import VideoGenJob
from modules.ws import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
from .identity_review import MEDIA_IDENTITY_ACCEPT_SCORE, score_character_frames

logger = get_logger(__name__)

_INFLIGHT: set[int] = set()

_BG = TaskBag("media.video_jobs")
_TERMINAL_STATUSES = ("succeeded", "failed", "result_unknown")
_RESULT_UNKNOWN_MESSAGE = "视频提交结果不确定，供应商可能已接单；为避免重复计费，系统没有自动重试"


async def _score_self_video(user_id: int, video_url: str, identity_path: str | None) -> tuple[str, int | None]:
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
        identity_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, identity_path)
        probe = await asyncio.to_thread(probe_video, local[0])
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
        score = await score_character_frames(user_id, identity_uri, tuple(frames))
        if score is not None:
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


async def _update_job(job_id: int, **fields) -> None:
    """用全新短会话更新任务行——后台任务比请求会话长寿，绝不复用调用方的 ``db``；行在读写之间被 GC（管理员 DELETE 等）则提前返回。终态保护：succeeded/failed 行不再被覆写，避免晚到的 poll 失败翻转已成功状态。"""

    async with SESSION_LOCAL() as db:
        job = await db.get(VideoGenJob, job_id)
        if job is None:
            return
        if job.status in _TERMINAL_STATUSES:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await db.commit()


async def _emit_ws_event(user_id: int, event_type: str, payload: dict) -> None:
    """将 WSEvent 行写入 PostgreSQL outbox；PostgreSQL NOTIFY 触发后由 ws_events worker 投递给已连接客户端，确保 REST 离线提交或 WS 中途重连的进度也能送达。"""
    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
        await db.commit()


async def _persist_media_status_message(session_id: str, content: str, media_json: str) -> None:
    """把后台完成的视频写为发起会话的送达行，使实时事件与历史水合看到同一形状；会话已删除时仅告警。"""
    try:
        conv_id = int(session_id)
    except (TypeError, ValueError):
        return
    try:
        async with SESSION_LOCAL() as db:
            db.add(
                Message(
                    conversation_id=conv_id,
                    role="system",
                    subtype=MEDIA_STATUS_SUBTYPE,
                    content=content,
                    media_json=media_json,
                ),
            )
            await db.commit()
    except Exception:
        logger.warning("failed to persist video status_media row", extra={"session_id": session_id}, exc_info=True)


async def _enqueue_channel_delivery(session_id: str | None, *, text: str, media: list[dict[str, str]]) -> None:
    """IM 会话的后台任务结果转待补发队列：绑定存在即落 ChannelDelivery 行，由渠道桥在对端
    下一条消息提供的新鲜回复上下文里补发（iLink reply-only，无法主动推送）；桌面会话无绑定，
    结果本就经 WS 事件与送达行抵达，直接跳过。投递状态独立于本行执行状态。"""
    if not session_id:
        return
    try:
        conv_id = int(session_id)
    except (TypeError, ValueError):
        return
    try:
        async with SESSION_LOCAL() as db:
            binding = (
                await db.execute(select(ChannelBinding).where(ChannelBinding.conversation_id == conv_id))
            ).scalar_one_or_none()
            if binding is None:
                return
            db.add(
                ChannelDelivery(
                    binding_id=binding.id,
                    peer_id="",
                    payload_json=ChannelDeliveryPayload.model_validate(
                        {"text": text, "media": media},
                    ).model_dump_json(),
                ),
            )
            await db.commit()
    except Exception:
        logger.warning("failed to enqueue channel delivery", extra={"session_id": session_id}, exc_info=True)


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
    model: str | None,
    aspect_ratio: str | None,
    identity_reference_path: str | None = None,
) -> "VideoGenJob":
    """插入 queued 任务行、向供应商提交并调度后台轮询任务，返回持久化行；任务 id 属于特定供应商，轮询始终钉在提交成功的供应商上。"""
    req = VideoGenRequest(
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        first_frame_image=first_frame_image,
        aspect_ratio=aspect_ratio,
        model=model,
    )

    params = {
        "duration": duration,
        "resolution": resolution,
        "first_frame_image": first_frame_image,
        "aspect_ratio": aspect_ratio,
        "identity_reference_path": identity_reference_path,
    }

    # 捕获提交实际胜出的供应商，轮询/下载都走它（task_id 跨供应商不通用）。
    submitted_provider: VideoGenProvider | None = None

    async def _submit(p: VideoGenProvider) -> VideoJobStatus:
        nonlocal submitted_provider
        submitted_provider = p
        return await p.submit(req)

    chain = await resolve_provider_chain(db, user_id, "video_gen")
    if not chain:
        raise MissingLlmConfigError("no provider configured for service 'video_gen'")
    head_cfg = chain[0]
    job = VideoGenJob(
        user_id=user_id,
        session_id=session_id,
        provider=head_cfg.provider_name,
        model=req.model or head_cfg.model,
        prompt=prompt,
        params_json=json.dumps(params),
        status="queued",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        # db=None：提交走独立短会话解析（_chain 已显式传入），请求会话不跨供应商 HTTP 等待持有连接
        submitted = await execute_with_fallback(None, user_id, "video_gen", call_fn=_submit, _chain=chain)
    except ProviderResultUnknownError:
        if submitted_provider is not None:
            job.provider = submitted_provider.provider_name
            job.model = req.model or submitted_provider.config.model
        job.status = "result_unknown"
        job.error_reason = "submit_result_unknown"
        job.error_message = _RESULT_UNKNOWN_MESSAGE
        await db.commit()
        await _emit_ws_event(
            user_id,
            "video_gen.failed",
            {
                "task_id": str(job.id),
                "error": _RESULT_UNKNOWN_MESSAGE,
                **({"session_id": session_id} if session_id else {}),
            },
        )
        return job
    except Exception as e:
        logger.exception("video submit failed", extra={"job_id": job.id})
        try:
            await _record_failure(job.id, reason="submit_failed", exc=e)
        except Exception as update_err:
            logger.exception("failed to mark job as failed", extra={"job_id": job.id, "error": str(update_err)})
        raise

    if submitted_provider is not None:
        job.provider = submitted_provider.provider_name
        job.model = req.model or submitted_provider.config.model
    job.provider_task_id = submitted.task_id
    await db.commit()

    t = asyncio.create_task(_poll_and_finalize(job.id))
    _BG.add(t, on_error=_on_video_task_error)
    track_user_task(user_id, t, cancel_on_maintenance=False)
    return job


_FAILURE_COPY: dict[str, str] = {
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
    user_id: int | None = None,
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
    await _update_job(job_id, status="failed", error_reason=reason, error_message=user_msg)
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
    if user_id is None:
        user_id = row.user_id if row else 0
    session_id = row.session_id if row is not None else None
    if user_id:
        await _emit_ws_event(
            user_id,
            "video_gen.failed",
            {"task_id": str(job_id), "error": user_msg, **({"session_id": session_id} if session_id else {})},
        )
    if session_id:
        await _enqueue_channel_delivery(session_id, text=f"视频生成失败（任务 {job_id}）：{user_msg}", media=[])


async def _finalize_best_video(job_id: int, *, warning: str | None = None) -> None:
    """已知最佳资产、任务终态和 WS outbox 同事务提交；重复恢复不会重复交付。"""
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or row.status in _TERMINAL_STATUSES:
            return
        storage_url = row.video_url
        parsed = asset_store.parse_companion_asset_path(storage_url) if storage_url else None
        if parsed is None or parsed[0] != row.user_id or asset_store.resolve_companion_asset_path(*parsed) is None:
            raise RuntimeError("best video asset is unavailable")
        client_url = client_asset_url(storage_url)
        media = [{"type": "video", "url": storage_url}]
        client_media = [{"type": "video", "url": client_url}]
        row.status = "succeeded"
        row.candidate_video_url = None
        row.candidate_file_id = None
        row.error_reason = "retry_result_unknown" if warning else None
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
        await db.commit()
    if session_id:
        await _enqueue_channel_delivery(session_id, text=f"视频已生成（任务 {job_id}）", media=media)


async def _evaluate_stored_video(job_id: int) -> str:
    """评分候选并持久化最佳资产；返回 complete/retry/stale/invalid。"""
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or row.status != "evaluating" or not row.candidate_video_url:
            return "invalid"
        candidate = row.candidate_video_url
        params = safe_json_loads(row.params_json or "{}", default={})
        identity_path = params.get("identity_reference_path") if isinstance(params, dict) else None
        user_id = row.user_id
    outcome, score = await _score_self_video(
        user_id,
        candidate,
        identity_path if isinstance(identity_path, str) else None,
    )
    cleanup: set[str] = set()
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or row.status != "evaluating" or row.candidate_video_url != candidate:
            return "invalid"
        if outcome == "stale":
            return "stale"
        previous_best = row.video_url
        if outcome != "invalid" and (previous_best is None or (score is not None and score > row.identity_best_score)):
            row.video_url = candidate
            row.file_id = row.candidate_file_id
            row.identity_best_score = score if score is not None else -1
            if previous_best and previous_best != candidate:
                cleanup.add(previous_best)
        elif candidate != previous_best:
            cleanup.add(candidate)
        row.candidate_video_url = None
        row.candidate_file_id = None
        # 仅可见身份偏差才付费重生成；本地处理失败或评分不可用不消耗重试预算。
        should_retry = (
            score is not None and score < MEDIA_IDENTITY_ACCEPT_SCORE
        ) and row.identity_retries_used < SETTINGS.character_media_regeneration_max_retries
        if should_retry:
            row.identity_retries_used += 1
            row.status = "retry_submitting"
            decision = "retry"
        else:
            decision = "complete" if row.video_url else "invalid"
        await db.commit()
    for path in cleanup:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(unlink_companion_asset, path)
    return decision


async def _submit_identity_retry(job_id: int, user_id: int, provider: VideoGenProvider) -> bool:
    """先持久化 retry_submitting，未知提交只保留已知最佳片段，绝不补发。"""
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None or row.status != "retry_submitting":
            return False
        params = safe_json_loads(row.params_json or "{}", default={})
        if not isinstance(params, dict):
            return False
        request = VideoGenRequest(
            prompt=row.prompt,
            duration=params.get("duration"),
            resolution=params.get("resolution"),
            first_frame_image=params.get("first_frame_image"),
            aspect_ratio=params.get("aspect_ratio"),
            model=row.model,
        )
    try:
        submitted = await provider.submit(request)
        if not submitted.task_id:
            raise ProviderResultUnknownError("video retry submission returned no task id")
    except Exception:
        logger.warning(
            "video identity retry outcome unavailable; keeping best known asset or failing the job",
            extra={"job_id": job_id},
            exc_info=True,
        )
        await _fail_or_keep_best(job_id, user_id, "provider_failed")
        return False
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id, with_for_update=True)
        if row is None or row.status != "retry_submitting":
            return False
        row.provider_task_id = submitted.task_id
        row.provider_file_id = None
        row.status = "processing"
        await db.commit()
    return True


async def _pinned_video_provider(job_id: int, user_id: int) -> VideoGenProvider | None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        if row is None:
            return None
        chain = await resolve_provider_chain(db, user_id, "video_gen")
        config = next((item for item in chain if item.provider_name == row.provider), None)
        if config is not None and row.model and row.model != config.model:
            config = replace(config, model=row.model)
    return resolve(ServiceType.video_gen, config.provider_name)(config) if config is not None else None


async def _fail_or_keep_best(job_id: int, user_id: int, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(VideoGenJob, job_id)
        has_best = bool(row and row.video_url)
    if has_best:
        await _finalize_best_video(job_id, warning="自动重生成未能完成，已保留此前最佳视频")
    else:
        await _record_failure(job_id, reason=reason, user_id=user_id)


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
        if job.status == "retry_submitting":
            await _fail_or_keep_best(job_id, user_id, "worker_failed")
            return
        if job.status in ("downloading", "evaluating"):
            candidate_path = job.candidate_video_url
            if not candidate_path:
                candidate_path = video_job_asset_path(user_id, job_id, job.identity_retries_used)
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
                    await _record_failure(job_id, reason="identity_changed", user_id=user_id)
                    return
                if decision == "invalid":
                    await _fail_or_keep_best(job_id, user_id, "quality_failed")
                    return
                provider = await _pinned_video_provider(job_id, user_id)
                if provider is None:
                    await _fail_or_keep_best(job_id, user_id, "provider_unavailable")
                    return
                if await _submit_identity_retry(job_id, user_id, provider):
                    await _poll_and_finalize_locked(job_id)
                return
        if job.status == "evaluating" and job.video_url:
            await _finalize_best_video(job_id)
            return
        if not provider_task_id:
            # 提交完成但 task_id 未持久化（极小概率，但保持防御），快速失败并给出明确原因，避免行一直处于 limbo。
            await _record_failure(job_id, reason="missing_task_id", user_id=user_id)
            return

        provider = await _pinned_video_provider(job_id, user_id)
        if provider is None:
            await _fail_or_keep_best(job_id, user_id, "provider_unavailable")
            return

        interval = SETTINGS.video_gen_poll_interval_seconds
        backoff_max = SETTINGS.video_gen_poll_backoff_max_seconds
        deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
        attempt = 0
        last_status: str | None = None
        while True:
            remaining = max(0.0, (deadline - utc_now()).total_seconds())
            if remaining <= 0:
                await _fail_or_keep_best(job_id, user_id, "timeout")
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
                await _fail_or_keep_best(job_id, user_id, "poll_failed")
                return

            if status.status == "succeeded":
                if job.status != "downloading":
                    async with SESSION_LOCAL() as db:
                        claimed = (
                            await db.execute(
                                update(VideoGenJob)
                                .where(VideoGenJob.id == job_id, VideoGenJob.status.in_(("queued", "processing")))
                                .values(status="downloading", provider_file_id=status.file_id),
                            )
                        ).rowcount
                        await db.commit()
                        if not claimed:
                            return
                try:
                    file_id, storage_url = await _download_and_store(
                        provider,
                        status.file_id,
                        download_url=status.download_url,
                        user_id=user_id,
                        job_id=job_id,
                        attempt=job.identity_retries_used,
                    )
                except Exception:
                    logger.exception("video download failed", extra={"job_id": job_id})
                    await _fail_or_keep_best(job_id, user_id, "download_failed")
                    return
                await _update_job(
                    job_id,
                    status="evaluating",
                    candidate_file_id=file_id,
                    candidate_video_url=storage_url,
                )
                await _poll_and_finalize_locked(job_id)
                return
            if status.status == "failed":
                await _fail_or_keep_best(job_id, user_id, "provider_failed")
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
                await _fail_or_keep_best(job_id, user_id, "timeout")
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
                    latest.identity_retries_used,
                )
                parsed = asset_store.parse_companion_asset_path(candidate)
                if parsed is not None and asset_store.resolve_companion_asset_path(*parsed) is not None:
                    logger.warning("stored video awaits recovery", extra={"job_id": job_id, "path": candidate})
                    return
            await _fail_or_keep_best(job_id, user_id, "worker_failed")
        except Exception:
            logger.exception("could not update video job after worker error", extra={"job_id": job_id})


async def _download_and_store(
    provider,
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
        if not file_id:
            raise RuntimeError("provider.poll succeeded without file_id or download_url")
        download_url = (await provider.fetch(file_id)).download_url
    data = await _stream_download(download_url)
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
                            ("queued", "processing", "downloading", "evaluating", "retry_submitting"),
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
