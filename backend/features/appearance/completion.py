"""角色资料就绪后的跨业务完成回调，由 bootstrap 装配。"""

from collections.abc import Awaitable, Callable

type CharacterReadyHandler = Callable[[int], Awaitable[None]]

_HANDLER: CharacterReadyHandler | None = None


def set_character_ready_handler(handler: CharacterReadyHandler) -> None:
    global _HANDLER
    _HANDLER = handler


async def notify_character_ready(user_id: int) -> None:
    if _HANDLER is None:
        raise RuntimeError("Character initialization completion is not configured")
    await _HANDLER(user_id)
