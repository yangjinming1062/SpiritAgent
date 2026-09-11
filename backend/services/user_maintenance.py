"""覆盖恢复的用户级维护边界。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from components import get_logger
from components.user_maintenance_runtime import (
    cancel_user_tasks,
    clear_user_maintenance,
    mark_user_maintenance,
    user_maintenance_lock,
    wait_for_user_requests,
)

logger = get_logger(__name__)


async def _await_quiescers(*coroutines) -> None:
    results = await asyncio.gather(*coroutines, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def _quiesce_runtime(user_id: int) -> None:
    # 局部导入避免 gateway/channels 在模块初始化时反向依赖维护服务。
    from services.channels import MANAGER as CHANNEL_MANAGER
    from services.gateway import terminate_user_gateway
    from services.ws import interrupt_user_cron_turns

    await _await_quiescers(
        terminate_user_gateway(user_id),
        CHANNEL_MANAGER.pause_user_bindings(user_id),
        interrupt_user_cron_turns(user_id),
        cancel_user_tasks(user_id),
    )
    # 已在边界建立期间结束的 REST 请求可能刚派生后台任务；再收一次保证清表前无遗漏。
    await wait_for_user_requests(user_id)
    await _await_quiescers(
        terminate_user_gateway(user_id),
        CHANNEL_MANAGER.pause_user_bindings(user_id),
        interrupt_user_cron_turns(user_id),
        cancel_user_tasks(user_id),
    )


def _invalidate_runtime_caches(user_id: int) -> None:
    from services.companion.interaction_stats import invalidate_user_interaction_stats
    from services.companion.should_act import invalidate_user_should_act
    from services.conversation.proactive_state import clear_user_proactive_state
    from services.scheduler.cron import invalidate_user_scheduler_state

    clear_user_proactive_state(user_id)
    invalidate_user_interaction_stats(user_id)
    invalidate_user_should_act(user_id)
    invalidate_user_scheduler_state(user_id)


@asynccontextmanager
async def user_maintenance(user_id: int) -> AsyncIterator[None]:
    """阻止新用户请求，停稳该用户的运行时任务，并在退出时从数据库真源恢复渠道。"""
    async with user_maintenance_lock(user_id):
        await mark_user_maintenance(user_id)
        try:
            await _quiesce_runtime(user_id)
            yield
        finally:
            try:
                _invalidate_runtime_caches(user_id)
            except Exception:
                logger.exception("failed to invalidate user runtime caches", extra={"user_id": user_id})
            finally:
                await clear_user_maintenance(user_id)

            from services.channels import MANAGER as CHANNEL_MANAGER

            try:
                await CHANNEL_MANAGER.resume_user_bindings(user_id)
            except Exception:
                logger.exception("failed to resume user channels after maintenance", extra={"user_id": user_id})
