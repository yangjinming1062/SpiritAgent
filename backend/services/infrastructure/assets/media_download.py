"""已提交媒体成品的下载重试，不重新提交供应商任务。"""

import asyncio

import httpx
from components import SETTINGS, download_capped, get_logger

logger = get_logger(__name__)
VIDEO_DOWNLOAD_ATTEMPTS = 3


async def download_media_result(url: str, *, max_bytes: int, timeout: float) -> bytes:
    """仅重试传输失败与 5xx，超限、协议及 4xx 保持确定失败。"""
    for attempt in range(VIDEO_DOWNLOAD_ATTEMPTS):
        try:
            return await download_capped(url, max_bytes=max_bytes, timeout=timeout)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            if attempt + 1 == VIDEO_DOWNLOAD_ATTEMPTS:
                raise
            logger.warning("media download retry", extra={"attempt": attempt + 1, "error_type": type(exc).__name__})
            await asyncio.sleep(SETTINGS.video_gen_poll_interval_seconds * 2**attempt)
