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


async def poll_video_task(
    provider: VideoGenProvider,
    task_id: str,
    *,
    before_poll: Callable[[], Awaitable[None]] | None = None,
) -> VideoJobStatus:
    """临时查询失败沿原句柄退避；调用方检查不参与供应商错误重试。"""
    attempt = 0
    last_status: str | None = None
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
            if status.status in ("succeeded", "failed"):
                return status
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
