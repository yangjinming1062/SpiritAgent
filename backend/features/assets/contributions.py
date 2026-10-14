"""业务模块提供资产引用与清理操作，资产回收只消费事务内结果。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

type References = Callable[[AsyncSession, int], Awaitable[set[str]]]
type Protection = Callable[[AsyncSession, int], Awaitable[dict[str, datetime]]]
type Forget = Callable[[AsyncSession, int, Sequence[str]], Awaitable[None]]


@dataclass(frozen=True)
class AssetContribution:
    name: str
    references: References
    cleanup: References | None = None
    protection: Protection | None = None
    forget: Forget | None = None


_CONTRIBUTIONS: tuple[AssetContribution, ...] | None = None


def configure_asset_contributions(contributions: Sequence[AssetContribution]) -> None:
    global _CONTRIBUTIONS
    names = [contribution.name for contribution in contributions]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate asset contribution")
    _CONTRIBUTIONS = tuple(contributions)


def asset_contributions() -> tuple[AssetContribution, ...]:
    if _CONTRIBUTIONS is None:
        raise RuntimeError("Asset contributions have not been registered")
    return _CONTRIBUTIONS


async def cleanup_obsolete_references(db: AsyncSession, user_id: int) -> set[str]:
    released: set[str] = set()
    for contribution in asset_contributions():
        if contribution.cleanup is not None:
            released |= await contribution.cleanup(db, user_id)
    return released


async def protected_asset_paths(db: AsyncSession, user_id: int) -> dict[str, datetime]:
    protected: dict[str, datetime] = {}
    for contribution in asset_contributions():
        if contribution.protection is not None:
            for path, until in (await contribution.protection(db, user_id)).items():
                protected[path] = max(until, protected.get(path, until))
    return protected


async def forget_deleted_references(db: AsyncSession, user_id: int, paths: Sequence[str]) -> None:
    for contribution in asset_contributions():
        if contribution.forget is not None:
            await contribution.forget(db, user_id, paths)
