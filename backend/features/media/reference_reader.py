"""当前身份验证与已确认参考读取端口，由 bootstrap 注入业务所有者。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .identity_contracts import CharacterCardSnapshot


@dataclass(frozen=True)
class MediaReferenceReaders:
    active_identity: Callable[[AsyncSession, int], Awaitable[str | None]]
    confirmed_paths: Callable[[AsyncSession, int], Awaitable[tuple[str, ...]]]
    identity_is_current: Callable[[AsyncSession, int, CharacterCardSnapshot], Awaitable[bool]]
    render_identity: Callable[[CharacterCardSnapshot | None], str]


_READERS: MediaReferenceReaders | None = None


def set_media_reference_readers(readers: MediaReferenceReaders) -> None:
    global _READERS
    _READERS = readers


def _readers() -> MediaReferenceReaders:
    if _READERS is None:
        raise RuntimeError("Media reference readers are not configured")
    return _READERS


async def active_identity_reference(db: AsyncSession, user_id: int) -> str | None:
    return await _readers().active_identity(db, user_id)


async def confirmed_media_reference_paths(db: AsyncSession, user_id: int) -> tuple[str, ...]:
    return await _readers().confirmed_paths(db, user_id)


async def character_snapshot_is_current(db: AsyncSession, user_id: int, snapshot: CharacterCardSnapshot) -> bool:
    return await _readers().identity_is_current(db, user_id, snapshot)


def render_character_identity(snapshot: CharacterCardSnapshot | None) -> str:
    return _readers().render_identity(snapshot)
