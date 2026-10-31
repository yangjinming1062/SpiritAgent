"""组合策划的情境读取、素材存在性与成品就绪通知，由 bootstrap 显式装配。"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import ActorClipSource, CinematicSource


class VisualPlanningContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_style: str = ""
    current_time: str = ""
    life_context: dict[str, Any] = Field(default_factory=dict)
    relevant_memories: list[dict[str, str]] = Field(default_factory=list)


PlanningReader = Callable[[int, ActorClipSource | CinematicSource], Awaitable[VisualPlanningContext]]
ReadyHandler = Callable[[int], None]
SceneExistenceReader = Callable[[AsyncSession, int, int], Awaitable[bool]]
_planning_reader: PlanningReader | None = None
_ready_handler: ReadyHandler | None = None
_preparation_handler: ReadyHandler | None = None
_recovery_handler: ReadyHandler | None = None
_scene_exists: SceneExistenceReader | None = None


def configure_visual_hooks(
    *,
    planning_reader: PlanningReader,
    ready_handler: ReadyHandler,
    preparation_handler: ReadyHandler,
    recovery_handler: ReadyHandler,
    scene_exists: SceneExistenceReader,
) -> None:
    global _planning_reader, _ready_handler, _preparation_handler, _recovery_handler, _scene_exists
    _planning_reader = planning_reader
    _ready_handler = ready_handler
    _preparation_handler = preparation_handler
    _recovery_handler = recovery_handler
    _scene_exists = scene_exists


async def read_planning_context(user_id: int, snapshot: ActorClipSource | CinematicSource) -> VisualPlanningContext:
    if _planning_reader is None:
        raise RuntimeError("影像策划的情境读取端口尚未装配")
    return await _planning_reader(user_id, snapshot)


def notify_visual_ready(user_id: int) -> None:
    if _ready_handler is not None:
        _ready_handler(user_id)


def request_base_motion_preparation(user_id: int) -> None:
    if _preparation_handler is not None:
        _preparation_handler(user_id)


def request_stage_recovery(user_id: int) -> None:
    """冻结素材已被删除时重选画面；读取路径只登记，不在查询里推进制作。"""

    if _recovery_handler is not None:
        _recovery_handler(user_id)


async def scene_reference_exists(db: AsyncSession, user_id: int, scene_id: int) -> bool:
    """未装配时按仍存在处理，避免读取路径误删有效引用。"""

    if _scene_exists is None:
        return True
    return await _scene_exists(db, user_id, scene_id)
