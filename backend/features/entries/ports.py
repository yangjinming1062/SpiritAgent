"""内容恢复后的派生索引由装配层提供。"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

EntryProjector = Callable[[AsyncSession, int], Awaitable[None]]
_projector: EntryProjector | None = None


def set_entry_projector(projector: EntryProjector) -> None:
    global _projector
    _projector = projector


async def rebuild_entry_projections(db: AsyncSession, user_id: int) -> None:
    if _projector is not None:
        await _projector(db, user_id)
