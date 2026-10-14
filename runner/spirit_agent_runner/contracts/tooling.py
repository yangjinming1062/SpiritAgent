"""工具定义与调用上下文；不依赖具体工具和运行时。"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .scope import SkillScope

DEFAULT_MAX_RESULT_SIZE_CHARS = 100_000


class ToolError(Exception):
    """工具执行前拒绝或已确认的执行失败。"""


@dataclass(frozen=True, slots=True)
class ToolContext:
    config: dict[str, Any] = field(default_factory=dict)
    task_id: str = "default"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    call_id: str | None = None
    skill_scope: SkillScope | None = None
    config_generation: int = 0


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    toolset_id: str
    schema: dict[str, Any] | Callable[[], dict[str, Any]]
    handler: Callable[[ToolContext, dict[str, Any]], Any]
    check_fn: Callable[[], bool] | None = None
    is_async: bool = False


def tool_error(message: str, **extra: Any) -> dict[str, Any]:
    return {"error": str(message)} | extra
