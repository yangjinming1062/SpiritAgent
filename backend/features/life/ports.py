"""生活事实的批量投影端口；跨域装配由 bootstrap 完成。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import LifeEventInput


@dataclass(frozen=True)
class LifeEventProjection:
    event: LifeEventInput
    occurred_at: datetime
    event_id: int


LifeEventsProjector = Callable[[AsyncSession, int, Sequence[LifeEventProjection]], Awaitable[None]]
_projector: LifeEventsProjector | None = None


def set_life_events_projector(projector: LifeEventsProjector) -> None:
    global _projector
    _projector = projector


async def project_life_events(db: AsyncSession, user_id: int, events: Sequence[LifeEventProjection]) -> None:
    if _projector is not None and events:
        await _projector(db, user_id, events)
