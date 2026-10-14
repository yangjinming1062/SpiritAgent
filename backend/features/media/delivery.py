"""媒体终态的事务内业务接收点，由 bootstrap 显式装配。"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import VideoJobSnapshot

type VideoResultHandler = Callable[[AsyncSession, VideoJobSnapshot], Awaitable[None]]

_RESULT_HANDLER: VideoResultHandler | None = None


def set_video_result_handler(handler: VideoResultHandler) -> None:
    global _RESULT_HANDLER
    _RESULT_HANDLER = handler


async def deliver_video_result(db: AsyncSession, result: VideoJobSnapshot) -> None:
    if result.session_id is None:
        return
    if _RESULT_HANDLER is None:
        raise RuntimeError("Video result delivery is not configured")
    await _RESULT_HANDLER(db, result)
