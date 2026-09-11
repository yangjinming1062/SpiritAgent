"""用户维护状态、活跃请求计数与后台任务归属；不依赖业务服务。"""

import asyncio

_MAINTENANCE_USERS: set[int] = set()
_ACTIVE_REQUESTS: dict[int, int] = {}
_USER_TASKS: dict[int, dict[asyncio.Task, bool]] = {}
_MAINTENANCE_LOCKS: dict[int, asyncio.Lock] = {}
_STATE_CHANGED = asyncio.Condition()


def is_user_in_maintenance(user_id: int) -> bool:
    return user_id in _MAINTENANCE_USERS


def user_maintenance_lock(user_id: int) -> asyncio.Lock:
    return _MAINTENANCE_LOCKS.setdefault(user_id, asyncio.Lock())


async def mark_user_maintenance(user_id: int) -> None:
    async with _STATE_CHANGED:
        _MAINTENANCE_USERS.add(user_id)


async def clear_user_maintenance(user_id: int) -> None:
    async with _STATE_CHANGED:
        _MAINTENANCE_USERS.discard(user_id)
        _STATE_CHANGED.notify_all()


async def begin_user_request(user_id: int) -> bool:
    """登记一个用户 REST 请求；维护已开始时拒绝新登记。"""
    async with _STATE_CHANGED:
        if user_id in _MAINTENANCE_USERS:
            return False
        _ACTIVE_REQUESTS[user_id] = _ACTIVE_REQUESTS.get(user_id, 0) + 1
        return True


async def end_user_request(user_id: int) -> None:
    async with _STATE_CHANGED:
        remaining = _ACTIVE_REQUESTS.get(user_id, 0) - 1
        if remaining > 0:
            _ACTIVE_REQUESTS[user_id] = remaining
        else:
            _ACTIVE_REQUESTS.pop(user_id, None)
        _STATE_CHANGED.notify_all()


async def wait_for_user_requests(user_id: int) -> None:
    async with _STATE_CHANGED:
        await _STATE_CHANGED.wait_for(lambda: _ACTIVE_REQUESTS.get(user_id, 0) == 0)


def track_user_task(user_id: int, task: asyncio.Task, *, cancel_on_maintenance: bool = True) -> None:
    """登记用户后台任务；可取消任务在维护期立即取消，付费生成任务则等待自然落地。"""
    tasks = _USER_TASKS.setdefault(user_id, {})
    tasks[task] = cancel_on_maintenance

    def _discard(done: asyncio.Task) -> None:
        owned = _USER_TASKS.get(user_id)
        if owned is None:
            return
        owned.pop(done, None)
        if not owned:
            _USER_TASKS.pop(user_id, None)

    task.add_done_callback(_discard)
    if cancel_on_maintenance and user_id in _MAINTENANCE_USERS and not task.done():
        task.cancel()


async def cancel_user_tasks(user_id: int) -> int:
    """取消可中断任务，并等待付费生成等不可中断任务与其派生任务全部落地。"""
    cancelled = 0
    while owned := _USER_TASKS.get(user_id):
        current = asyncio.current_task()
        pending = [task for task in owned if task is not current]
        if not pending:
            break
        for task in pending:
            if owned.get(task, True) and not task.done():
                task.cancel()
                cancelled += 1
        await asyncio.gather(*pending, return_exceptions=True)
    return cancelled
