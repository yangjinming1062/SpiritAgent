"""素材就绪后投影生活情境并准备有限基础动态；普通媒体选择由客户端导演负责。"""

from typing import Any

from features.action_library.service import ensure_builtin_actions
from infrastructure.database import SESSION_LOCAL
from runtime.background import TaskScope

from workflows.life_presentation import evaluate_life_presentation
from workflows.visual_preparation import schedule_base_motion_preparation

_TASKS = TaskScope("visuals.events")


def schedule_presentation_refresh(user_id: int) -> None:
    _TASKS.spawn(
        user_id,
        lambda: evaluate_life_presentation(user_id, force=True),
        name=f"visual.presentation.{user_id}",
        user_id=user_id,
    )


async def initialize_visuals(user_id: int, _payload: dict[str, Any]) -> None:
    async with SESSION_LOCAL() as db:
        await ensure_builtin_actions(db, user_id)
        await db.commit()
    schedule_base_motion_preparation(user_id)
    await evaluate_life_presentation(user_id, force=True)


async def drain_visual_events() -> None:
    await _TASKS.drain()
