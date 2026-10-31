"""公共视频句柄查询；只续查已接单任务，不提交生成。"""

import asyncio
from collections.abc import Awaitable, Callable

from infrastructure.ai.error_classifier import FailoverReason, classify_api_error
from infrastructure.ai.providers.base import VideoGenProvider, VideoJobStatus
from infrastructure.backoff import backoff_for_poll
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from infrastructure.redact import redact_sensitive_text

logger = get_logger(__name__)

# 成功但缺地址尚不可交付；有限续查后交回队列，保留原句柄。
_SUCCESS_WITHOUT_URL_RECHECKS = 3


async def poll_video_task(
    provider: VideoGenProvider,
    task_id: str,
    *,
    before_poll: Callable[[], Awaitable[None]] | None = None,
) -> VideoJobStatus:
    """临时查询失败沿原句柄退避；调用方检查不参与供应商错误重试。"""
    attempt = 0
    last_status: str | None = None
    missing_url_attempts = 0
    log_extra = {"provider": provider.config.provider_name, "model": provider.config.model, "task_id": task_id}
    while True:
        if before_poll is not None:
            await before_poll()
        try:
            status = await provider.poll(task_id)
        except Exception as exc:
            if classify_api_error(exc).reason not in (
                FailoverReason.timeout,
                FailoverReason.rate_limit,
                FailoverReason.overloaded,
                FailoverReason.server_error,
            ):
                raise
            logger.warning("video task poll failed; retrying", extra=log_extra, exc_info=True)
            current = "poll_error"
        else:
            if status.status == "failed":
                logger.warning(
                    "video task failed at provider",
                    extra={**log_extra, "error": redact_sensitive_text(status.error)},
                )
            if status.status == "succeeded" and not status.download_url:
                missing_url_attempts += 1
                if missing_url_attempts >= _SUCCESS_WITHOUT_URL_RECHECKS:
                    raise RuntimeError("视频已完成但下载地址暂未提供，保留原任务等待继续核对")
                logger.warning(
                    "video task succeeded without download URL; rechecking same handle",
                    extra={**log_extra, "attempt": missing_url_attempts},
                )
                current = "processing"
            elif status.status in ("succeeded", "failed"):
                return status
            else:
                missing_url_attempts = 0
                current = status.status
        if last_status is not None and current != last_status:
            attempt = 0
        elif last_status is not None:
            attempt += 1
        last_status = current
        await asyncio.sleep(
            backoff_for_poll(
                attempt,
                base_interval=SETTINGS.video_gen_poll_interval_seconds,
                max_interval=SETTINGS.video_gen_poll_backoff_max_seconds,
            ),
        )
