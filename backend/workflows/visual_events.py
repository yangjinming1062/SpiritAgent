"""素材就绪后投影生活情境并准备有限基础动态；普通媒体选择由客户端导演负责。"""

import asyncio
from typing import Any

from features.action_library.service import ensure_builtin_actions
from infrastructure.database import SESSION_LOCAL
from runtime.task_supervisor import TASK_SUPERVISOR

from workflows.life_presentation import evaluate_life_presentation
from workflows.visual_preparation import schedule_base_motion_preparation

_TASKS: dict[int, asyncio.Task[None]] = {}


def schedule_presentation_refresh(user_id: int) -> None:
    if user_id in _TASKS and not _TASKS[user_id].done():
        return
    task = asyncio.create_task(evaluate_life_presentation(user_id, force=True), name=f"visual.presentation.{user_id}")
    _TASKS[user_id] = task
    TASK_SUPERVISOR.register(task, owner="visuals.events", user_id=user_id)
    task.add_done_callback(lambda finished: _TASKS.pop(user_id, None) if _TASKS.get(user_id) is finished else None)


async def initialize_visuals(user_id: int, _payload: dict[str, Any]) -> None:
    async with SESSION_LOCAL() as db:
        await ensure_builtin_actions(db, user_id)
        await db.commit()
    schedule_base_motion_preparation(user_id)
    await evaluate_life_presentation(user_id, force=True)


async def drain_visual_events() -> None:
    await TASK_SUPERVISOR.drain(owner="visuals.events")
