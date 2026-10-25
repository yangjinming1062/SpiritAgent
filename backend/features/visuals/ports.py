"""组合策划的情境读取与成品就绪通知，由 bootstrap 显式装配。"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .schemas import ComboSnapshot


class VisualPlanningContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_style: str = ""
    current_time: str = ""
    life_context: dict[str, Any] = Field(default_factory=dict)
    relevant_memories: list[dict[str, str]] = Field(default_factory=list)


PlanningReader = Callable[[int, ComboSnapshot], Awaitable[VisualPlanningContext]]
ReadyHandler = Callable[[int], None]
_planning_reader: PlanningReader | None = None
_ready_handler: ReadyHandler | None = None


def configure_visual_hooks(
    *,
    planning_reader: PlanningReader,
    ready_handler: ReadyHandler,
) -> None:
    global _planning_reader, _ready_handler
    _planning_reader = planning_reader
    _ready_handler = ready_handler


async def read_planning_context(user_id: int, snapshot: ComboSnapshot) -> VisualPlanningContext:
    if _planning_reader is None:
        raise RuntimeError("影像策划的情境读取端口尚未装配")
    return await _planning_reader(user_id, snapshot)


def notify_visual_ready(user_id: int) -> None:
    if _ready_handler is not None:
        _ready_handler(user_id)
