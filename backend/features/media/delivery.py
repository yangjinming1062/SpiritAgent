"""媒体终态的事务内业务接收点，由 bootstrap 显式装配。"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import VideoJobSnapshot
from .review_views import MediaJobView

type VideoResultHandler = Callable[[AsyncSession, VideoJobSnapshot], Awaitable[None]]

_VIDEO_HANDLER: VideoResultHandler | None = None


def set_video_result_handler(handler: VideoResultHandler) -> None:
    global _VIDEO_HANDLER
    _VIDEO_HANDLER = handler


async def deliver_video_result(db: AsyncSession, result: VideoJobSnapshot) -> None:
    if result.session_id is None:
        return
    if _VIDEO_HANDLER is None:
        raise RuntimeError("Video result delivery is not configured")
    await _VIDEO_HANDLER(db, result)


type ImageResultHandler = Callable[[AsyncSession, MediaJobView], Awaitable[None]]

_IMAGE_HANDLER: ImageResultHandler | None = None


def set_image_result_handler(handler: ImageResultHandler) -> None:
    global _IMAGE_HANDLER
    _IMAGE_HANDLER = handler


async def deliver_image_result(db: AsyncSession, result: MediaJobView) -> None:
    if _IMAGE_HANDLER is None:
        raise RuntimeError("Image result delivery is not configured")
    await _IMAGE_HANDLER(db, result)
