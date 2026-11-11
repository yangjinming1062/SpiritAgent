"""账户级维护边界：删除等管理操作在此停稳该用户的运行时。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from features.realtime_voice.session import CALL_MANAGER
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
    await CALL_MANAGER.end_all(user_id)
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
    await wait_user_turns(user_id)
    await cancel_user_tasks(user_id)


@asynccontextmanager
async def user_maintenance(user_id: int) -> AsyncIterator[None]:
    """账户删除期间阻止新请求并停稳运行时；删除失败时恢复被维护挡下的工作。"""
    async with user_maintenance_lock(user_id):
        await mark_user_maintenance(user_id)
        deleted = False
        try:
            await _quiesce_runtime(user_id)
            yield
            deleted = True
        finally:
            try:
                RUNTIME_LIFECYCLE.invalidate_user(user_id)
            finally:
                await clear_user_maintenance(user_id)
            if not deleted:
                await RUNTIME_LIFECYCLE.resume_user(user_id)
