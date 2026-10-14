"""单进程资产写入保护；逻辑调用方和实际制作任务都结束后，未引用文件才能回收。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from threading import RLock
from weakref import WeakValueDictionary

_owners: ContextVar[tuple[asyncio.Task, ...]] = ContextVar("asset_write_owners", default=())
_writes: dict[str, set[asyncio.Task] | None] = {}
_guard = RLock()
_user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def user_asset_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


@contextmanager
def asset_write_context(*, inherit: bool = True) -> Iterator[None]:
    owners = tuple(owner for owner in _owners.get() if not owner.done()) if inherit else ()
    current = asyncio.current_task()
    if current is not None and current not in owners:
        owners = (*owners, current)
    token = _owners.set(owners)
    try:
        yield
    finally:
        _owners.reset(token)


def protect_asset_write(path: str) -> None:
    owners = set(_owners.get())
    with suppress(RuntimeError):
        if current := asyncio.current_task():
            owners.add(current)
    with _guard:
        if path not in _writes:
            _writes[path] = owners or None
        elif (previous := _writes[path]) is not None:
            previous.update(owners)


def forget_asset_write(path: str) -> None:
    with _guard:
        _writes.pop(path, None)


def asset_write_in_progress(path: str) -> bool:
    with _guard:
        if path not in _writes:
            return False
        owners = _writes[path]
        if owners is not None and all(owner.done() for owner in owners):
            del _writes[path]
            return False
        # 没有协程归属的同步写入必须显式结束保护，不能猜测提交已完成。
        return True


def prune_completed_asset_writes() -> None:
    with _guard:
        for path, owners in list(_writes.items()):
            if owners is not None and all(owner.done() for owner in owners):
                del _writes[path]
