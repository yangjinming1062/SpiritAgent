"""覆盖恢复的用户级维护边界。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from components import (
    cancel_user_tasks,
    clear_user_maintenance,
    get_logger,
    mark_user_maintenance,
    user_maintenance_lock,
    wait_for_user_requests,
)
from modules.ws import COMPANION_TURN_EVENT

from services.adapters.desktop import terminate_remote_sessions, terminate_user_gateway
from services.adapters.scheduler import invalidate_user_scheduler_state
from services.application.actions import resume_desktop_reviews, resume_proposal_reviews
from services.application.generation import (
    resume_user_character_extraction,
    resume_user_desktop_video_jobs,
    resume_user_dynamic_actions,
    resume_user_scene_jobs,
    resume_user_video_jobs,
)
from services.domains.companion import (
    clear_user_proactive_state,
    invalidate_user_interaction_stats,
    invalidate_user_should_act,
)
from services.domains.memory import invalidate_memory_review_locks
from services.infrastructure.event_store import interrupt_user_event_tasks

logger = get_logger(__name__)


async def _stop_user_runtime(user_id: int) -> None:
    """并行停稳网关、主动回合与用户任务；全部收敛后再抛出首个失败。"""
    results = await asyncio.gather(
        terminate_user_gateway(user_id),
        terminate_remote_sessions(user_id),
        interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT),
        cancel_user_tasks(user_id),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def _quiesce_runtime(user_id: int) -> None:
    await _stop_user_runtime(user_id)
    # 已在边界建立期间结束的 REST 请求可能刚派生后台任务；再收一次保证清表前无遗漏。
    await wait_for_user_requests(user_id)
    await _stop_user_runtime(user_id)


def _invalidate_runtime_caches(user_id: int) -> None:
    clear_user_proactive_state(user_id)
    invalidate_user_interaction_stats(user_id)
    invalidate_user_should_act(user_id)
    invalidate_memory_review_locks(user_id)
    invalidate_user_scheduler_state(user_id)


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
                _invalidate_runtime_caches(user_id)
            except Exception:
                logger.exception("failed to invalidate user runtime caches", extra={"user_id": user_id})
            finally:
                await clear_user_maintenance(user_id)

            try:
                await resume_user_dynamic_actions(user_id)
            except Exception:
                logger.exception("failed to resume dynamic actions after maintenance", extra={"user_id": user_id})
            for resume in (
                resume_user_scene_jobs,
                resume_user_character_extraction,
                resume_user_video_jobs,
                resume_proposal_reviews,
                resume_user_desktop_video_jobs,
                resume_desktop_reviews,
            ):
                try:
                    await resume(user_id)
                except Exception:
                    logger.exception("failed to resume generation after maintenance", extra={"user_id": user_id})
