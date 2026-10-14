"""伙伴引导读取身份确认状态，由装配层接入资料拥有者。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class AppearanceReadiness:
    seed_fullbody_url: str
    is_fullbody_confirmed: bool


AppearanceReadinessReader = Callable[[AsyncSession, int], Awaitable[AppearanceReadiness | None]]
_readiness_reader: AppearanceReadinessReader | None = None


def set_appearance_readiness_reader(reader: AppearanceReadinessReader) -> None:
    global _readiness_reader
    _readiness_reader = reader


async def read_appearance_readiness(db: AsyncSession, user_id: int) -> AppearanceReadiness | None:
    if _readiness_reader is None:
        raise RuntimeError("Appearance readiness reader has not been registered")
    return await _readiness_reader(db, user_id)
