"""生活事件的跨域投影端口。

生活领域只负责事实和状态；记忆投影由 bootstrap 装配的流程回调完成，避免
生活模块反向依赖记忆模块。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import LifeEventInput

LifeEventProjector = Callable[[AsyncSession, int, LifeEventInput, datetime], Awaitable[None]]

_projector: LifeEventProjector | None = None


def set_life_event_projector(projector: LifeEventProjector) -> None:
    global _projector
    _projector = projector


async def project_life_event(
    db: AsyncSession,
    user_id: int,
    event: LifeEventInput,
    occurred_at: datetime,
) -> None:
    if _projector is not None and event.is_meaningful:
        await _projector(db, user_id, event, occurred_at)
