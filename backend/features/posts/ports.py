"""动态发布后的跨域通知端口。"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

PostPublishedHandler = Callable[[AsyncSession, int, str], Awaitable[None]]
_handler: PostPublishedHandler | None = None


def set_post_published_handler(handler: PostPublishedHandler) -> None:
    global _handler
    _handler = handler


async def notify_post_published(db: AsyncSession, user_id: int, post_id: str) -> None:
    if _handler is not None:
        await _handler(db, user_id, post_id)
