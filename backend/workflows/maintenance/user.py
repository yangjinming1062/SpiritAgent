"""覆盖恢复的用户级维护边界。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from runtime.channels import terminate_remote_sessions, terminate_user_gateway
from runtime.chat_accounts import wait_user_turns
from runtime.events.contracts import COMPANION_TURN_EVENT
from runtime.events.loop import interrupt_user_event_tasks
from runtime.lifecycle import RUNTIME_LIFECYCLE
from runtime.user_maintenance import (
    cancel_user_tasks,
    clear_user_maintenance,
    mark_user_maintenance,
    user_maintenance_lock,
    wait_for_user_requests,
)


async def _stop_user_runtime(user_id: int) -> None:
    """停稳连接与主动回合，等待用户回合后再清理后台任务。"""
    results = await asyncio.gather(
        terminate_user_gateway(user_id),
        terminate_remote_sessions(user_id),
        interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT),
        return_exceptions=True,
    )
    await wait_user_turns(user_id)
    results.extend(await asyncio.gather(cancel_user_tasks(user_id), return_exceptions=True))
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def _quiesce_runtime(user_id: int) -> None:
    await _stop_user_runtime(user_id)
    # 已在边界建立期间结束的 REST 请求可能刚派生后台任务；再收一次保证清表前无遗漏。
    await wait_for_user_requests(user_id)
    await _stop_user_runtime(user_id)


@asynccontextmanager
async def user_maintenance(user_id: int) -> AsyncIterator[None]:
    """阻止新用户请求，停稳该用户的运行时任务，并在退出时从数据库真源恢复被维护挡下的资产制作。"""
    async with user_maintenance_lock(user_id):
        await mark_user_maintenance(user_id)
        try:
            await _quiesce_runtime(user_id)
            yield
        finally:
            try:
                RUNTIME_LIFECYCLE.invalidate_user(user_id)
            finally:
                await clear_user_maintenance(user_id)
            await RUNTIME_LIFECYCLE.resume_user(user_id)
