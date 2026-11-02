"""引导跳过环境步骤时请求默认初始场景，由装配层接入场景服务。"""

from collections.abc import Awaitable, Callable

InitialSceneEnsurer = Callable[[int], Awaitable[object]]
_ensurer: InitialSceneEnsurer | None = None


def set_initial_scene_ensurer(ensurer: InitialSceneEnsurer) -> None:
    global _ensurer
    _ensurer = ensurer


async def ensure_initial_scene(user_id: int) -> None:
    if _ensurer is None:
        raise RuntimeError("Initial scene ensurer has not been registered")
    await _ensurer(user_id)
