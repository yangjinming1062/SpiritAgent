import asyncio
import json
from dataclasses import replace
from datetime import timedelta

from components import (
    SESSION_LOCAL,
    SETTINGS,
    TaskBag,
    backoff_for_poll,
    download_capped,
    get_logger,
    save_file,
    utc_now,
)
from components.user_maintenance_runtime import track_user_task
from modules.conversation import Message
from modules.media import VideoGenJob
from modules.ws import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.conversation import MEDIA_STATUS_SUBTYPE
from services.llm import (
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

logger = get_logger(__name__)

_INFLIGHT: set[int] = set()

_BG = TaskBag("media.video_jobs")
_TERMINAL_STATUSES = ("succeeded", "failed", "result_unknown")
_RESULT_UNKNOWN_MESSAGE = "视频提交结果不确定，供应商可能已接单；为避免重复计费，系统没有自动重试"


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
        submitted = await execute_with_fallback(db, user_id, "video_gen", call_fn=_submit, _chain=chain)
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


# In-flight 集合：进程中途重启时，多个协程可能竞争 finalize 同一任务。第一个进入的注册，后续提前退出，避免重复下载或重复 WSEvent；集合驻留在进程内存（重启即丢失——重启后由 resume_pending_video_jobs 走 DB 重建）。


async def _poll_and_finalize(job_id: int) -> None:
    """后台主循环：轮询供应商、成功后下载、写 WSEvent。状态机：``queued`` → ``processing`` → ``downloading`` → ``succeeded``/``failed``；``downloading`` 故意排除在 resume 集合外，避免重连任务重启下载半段。"""
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

    async def _evt(event_type: str, payload: dict) -> None:
        await _emit_ws_event(user_id, event_type, payload)

    try:
        if not provider_task_id:
            # 提交完成但 task_id 未持久化（极小概率，但保持防御），快速失败并给出明确原因，避免行一直处于 limbo。
            await _record_failure(job_id, reason="missing_task_id", user_id=user_id)
            return

        async with SESSION_LOCAL() as db:
            job_row = await db.get(VideoGenJob, job_id)
            provider_name = job_row.provider if job_row else ""
            job_model = (job_row.model if job_row else "") or ""
            chain = await resolve_provider_chain(db, user_id, "video_gen")
            provider_cfg = next((cfg for cfg in chain if cfg.provider_name == provider_name), None)
        # 将配置钉在任务提交时的 model 上。供应商可能按模型名切换 API 协议（MiniMax v1 vs H3 v2），用户中途改动 model 配置会让重新解析的链命中错误接口。
        if provider_cfg is not None and job_model and job_model != provider_cfg.model:
            provider_cfg = replace(provider_cfg, model=job_model)
        if provider_cfg is None:
            await _record_failure(job_id, reason="provider_unavailable", user_id=user_id)
            return
        provider = resolve(ServiceType.video_gen, provider_cfg.provider_name)(provider_cfg)

        interval = SETTINGS.video_gen_poll_interval_seconds
        backoff_max = SETTINGS.video_gen_poll_backoff_max_seconds
        deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
        attempt = 0
        last_status: str | None = None
        while True:
            remaining = max(0.0, (deadline - utc_now()).total_seconds())
            if remaining <= 0:
                await _record_failure(job_id, reason="timeout", user_id=user_id)
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
                await _record_failure(job_id, reason="poll_failed", user_id=user_id)
                return

            if status.status == "succeeded":
                # 在 ``downloading`` 状态认领该行——resume_pending_video_jobs 会跳过任何非 queued/processing 状态的任务，因此中途崩溃不会触发第二次下载。
                async with SESSION_LOCAL() as db:
                    claimed = (
                        await db.execute(
                            update(VideoGenJob)
                            .where(
                                VideoGenJob.id == job_id,
                                VideoGenJob.status.notin_((*_TERMINAL_STATUSES, "downloading")),
                            )
                            .values(status="downloading", provider_file_id=status.file_id),
                        )
                    ).rowcount
                    await db.commit()
                    if not claimed:
                        return
                try:
                    file_id, public_url = await _download_and_store(
                        provider,
                        status.file_id,
                        download_url=status.download_url,
                    )
                except Exception:
                    logger.exception("video download failed", extra={"job_id": job_id})
                    await _record_failure(job_id, reason="download_failed", user_id=user_id)
                    return
                await _update_job(job_id, status="succeeded", file_id=file_id, video_url=public_url)
                session_id = getattr(job, "session_id", None)
                media = [{"type": "video", "url": public_url}]
                if session_id:
                    await _persist_media_status_message(
                        session_id,
                        f"[视频已生成 task {job_id}] {public_url}",
                        json.dumps(media, ensure_ascii=False),
                    )
                await _evt(
                    "video_gen.completed",
                    {
                        "task_id": str(job_id),
                        "url": public_url,
                        **({"session_id": session_id} if session_id else {}),
                        "media": media,
                    },
                )
                logger.info("video job succeeded", extra={"job_id": job_id, "file_id": file_id})
                return
            if status.status == "failed":
                await _record_failure(job_id, reason="provider_failed", user_id=user_id, exc_text=status.error)
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
                await _record_failure(job_id, reason="timeout", user_id=user_id)
                return
            await asyncio.sleep(sleep_for)
    except Exception:
        logger.exception("unhandled exception in video poll worker", extra={"job_id": job_id})
        await _record_failure(job_id, reason="worker_failed", user_id=user_id)


async def _download_and_store(provider, file_id: str | None, *, download_url: str | None = None) -> tuple[str, str]:
    """从供应商下载视频字节（须在 URL 窗口内）并通过 ``components.save_file`` 本地持久化。MiniMax-H3 v2 在成功路径直接返回 URL（填 ``download_url``），跳过额外 ``fetch()``；旧版 MiniMax-Hailuo v1 把 URL 藏在 ``files/retrieve`` 接口后（填 ``file_id``）。"""
    if download_url:
        asset_content_type = "video/mp4"
    else:
        if not file_id:
            raise RuntimeError("provider.poll succeeded without file_id or download_url")
        asset = await provider.fetch(file_id)
        download_url = asset.download_url
        asset_content_type = asset.content_type or "video/mp4"
    data = await _stream_download(download_url)
    return save_file(data, session_id="", content_type=asset_content_type, ext="mp4")


async def _stream_download(url: str) -> bytes:
    """受限下载，上限为 ``video_gen_download_max_bytes``——LLM 默认 30–60s 读取超时对慢速 200MB 视频远不够，改用 10 分钟。"""
    cap = SETTINGS.video_gen_download_max_bytes
    return await download_capped(url, max_bytes=cap, timeout=600.0)


async def resume_pending_video_jobs() -> None:
    """扫描 queued/processing 任务并重新挂载轮询任务，由 FastAPI lifespan 启动时调用；``downloading`` 是恢复交接点——下载中断但没完成的任务在 9h 供应商 URL 窗口过期后不可恢复，故直接标记失败而非空转。"""

    async with SESSION_LOCAL() as db:
        stuck = await db.execute(
            VideoGenJob.__table__.update()
            .where(VideoGenJob.status == "downloading")
            .values(
                status="failed",
                error_reason="download_interrupted",
                error_message=_FAILURE_COPY["download_interrupted"],
            ),
        )
        rows = (
            (await db.execute(select(VideoGenJob).where(VideoGenJob.status.in_(("queued", "processing")))))
            .scalars()
            .all()
        )
        jobs = [(r.id, r.user_id) for r in rows]
        await db.commit()
    if stuck.rowcount:
        logger.warning("marked downloading jobs failed during resume", extra={"count": stuck.rowcount})
    for job_id, user_id in jobs:
        t = asyncio.create_task(_poll_and_finalize(job_id))
        _BG.add(t, on_error=_on_video_task_error)
        track_user_task(user_id, t, cancel_on_maintenance=False)
    if jobs:
        logger.info("Resumed pending video jobs", extra={"count": len(jobs)})
