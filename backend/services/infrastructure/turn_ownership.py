"""单进程会话互斥：桌面历史操作、无头回合和删除共享同一把锁。"""

import asyncio
from weakref import WeakValueDictionary

_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def conversation_lock(session_id: str) -> asyncio.Lock:
    key = str(int(session_id))
    return _LOCKS.setdefault(key, asyncio.Lock())


def conversation_is_running(session_id: str) -> bool:
    lock = _LOCKS.get(str(int(session_id)))
    return lock is not None and lock.locked()
