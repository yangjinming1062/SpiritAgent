"""账户内音色制作的取消与任务归属。"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from infrastructure.async_io import cancel_tasks
from runtime.user_maintenance import track_user_task

_operations: dict[tuple[int, str], asyncio.Task] = {}
_cancelled_operations: dict[tuple[int, str], float] = {}
_CANCELLED_OPERATION_TTL_SECONDS = 300
_CANCELLED_OPERATION_LIMIT = 1024


def _prune_cancelled_operations() -> None:
    now = time.monotonic()
    while _cancelled_operations:
        key = next(iter(_cancelled_operations))
        if _cancelled_operations[key] > now:
            break
        del _cancelled_operations[key]


def validate_voice_operation_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("音色操作标识无效") from exc


async def run_voice_operation[T](user_id: int, operation_id: str, operation: Callable[[], Awaitable[T]]) -> T:
    key = (user_id, validate_voice_operation_id(operation_id))
    _prune_cancelled_operations()
    if key in _cancelled_operations:
        raise ValueError("声音制作已取消")
    if key in _operations:
        raise ValueError("该音色操作正在进行，请等待结果")
    task = asyncio.create_task(operation(), name=f"voice-operation:{user_id}:{key[1]}")
    _operations[key] = task
    track_user_task(user_id, task, owner="media.voice")
    try:
        return await task
    except asyncio.CancelledError as exc:
        # RPC 取消的是制作子任务，保留连接请求本身以返回明确的取消结果。
        if asyncio.current_task() is not None and asyncio.current_task().cancelling():
            raise
        raise ValueError("声音制作已取消") from exc
    finally:
        if _operations.get(key) is task:
            del _operations[key]


def cancel_voice_operation(user_id: int, operation_id: str) -> bool:
    key = (user_id, validate_voice_operation_id(operation_id))
    _prune_cancelled_operations()
    if key in _cancelled_operations:
        return True
    task = _operations.get(key)
    # 取消可能先于制作到达，也可能落在描述生成与试听制作之间。
    _cancelled_operations[key] = time.monotonic() + _CANCELLED_OPERATION_TTL_SECONDS
    while len(_cancelled_operations) > _CANCELLED_OPERATION_LIMIT:
        del _cancelled_operations[next(iter(_cancelled_operations))]
    if task is not None:
        cancel_tasks((task,))
    return True
