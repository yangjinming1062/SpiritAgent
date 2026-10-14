"""删除衣柜资料时，同事务收敛关联动作资产的显式协作接口。"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from features.media.contracts import RemovedActionVideoTask

OutfitActionCleanup = Callable[[AsyncSession, int, int], Awaitable[list[RemovedActionVideoTask]]]
_cleanup_actions: OutfitActionCleanup | None = None


def set_outfit_action_cleanup(cleanup: OutfitActionCleanup) -> None:
    global _cleanup_actions
    _cleanup_actions = cleanup


async def delete_outfit_actions(db: AsyncSession, user_id: int, outfit_id: int) -> list[RemovedActionVideoTask]:
    if _cleanup_actions is None:
        raise RuntimeError("Outfit action cleanup has not been registered")
    return await _cleanup_actions(db, user_id, outfit_id)
