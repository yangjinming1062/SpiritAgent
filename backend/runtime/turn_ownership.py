"""单进程会话互斥：桌面历史操作、无头回合和删除共享同一把锁。"""

import asyncio
from weakref import WeakValueDictionary

_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_CALLS: dict[str, object] = {}


def reserve_call(session_id: str, owner: object) -> None:
    if session_id in _CALLS:
        raise RuntimeError("Conversation already reserved for a call")
    _CALLS[session_id] = owner


def release_call(session_id: str, owner: object) -> None:
    if _CALLS.get(session_id) is owner:
        del _CALLS[session_id]


def conversation_call_conflict(session_id: str, owner: object | None = None) -> bool:
    return session_id in _CALLS and _CALLS[session_id] is not owner


def conversation_lock(session_id: str) -> asyncio.Lock:
    key = str(int(session_id))
    return _LOCKS.setdefault(key, asyncio.Lock())


def conversation_is_running(session_id: str) -> bool:
    if conversation_call_conflict(session_id):
        return True
    lock = _LOCKS.get(str(int(session_id)))
    return lock is not None and lock.locked()
