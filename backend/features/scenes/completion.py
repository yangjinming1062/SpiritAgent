"""场景首次准备完成后的跨业务回调，由 bootstrap 装配。"""

from collections.abc import Awaitable, Callable

type SceneReadyHandler = Callable[[int], Awaitable[None]]

_HANDLER: SceneReadyHandler | None = None


def set_scene_ready_handler(handler: SceneReadyHandler) -> None:
    global _HANDLER
    _HANDLER = handler


async def notify_scene_ready(user_id: int) -> None:
    if _HANDLER is not None:
        await _HANDLER(user_id)
