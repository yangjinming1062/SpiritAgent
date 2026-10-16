"""角色资料与着装就绪后的跨业务完成回调，由 bootstrap 装配。"""

from collections.abc import Awaitable, Callable

type CharacterReadyHandler = Callable[[int], Awaitable[None]]
type OutfitReadyHandler = Callable[[int, int], Awaitable[None]]

_HANDLER: CharacterReadyHandler | None = None
_OUTFIT_HANDLER: OutfitReadyHandler | None = None


def set_character_ready_handler(handler: CharacterReadyHandler) -> None:
    global _HANDLER
    _HANDLER = handler


async def notify_character_ready(user_id: int) -> None:
    if _HANDLER is None:
        raise RuntimeError("Character initialization completion is not configured")
    await _HANDLER(user_id)


def set_outfit_ready_handler(handler: OutfitReadyHandler) -> None:
    global _OUTFIT_HANDLER
    _OUTFIT_HANDLER = handler


async def notify_outfit_ready(user_id: int, outfit_id: int) -> None:
    if _OUTFIT_HANDLER is None:
        raise RuntimeError("Outfit action preparation is not configured")
    await _OUTFIT_HANDLER(user_id, outfit_id)
