"""会话、记忆与任务共享的作用域和不可变交换值。"""

from dataclasses import dataclass
from typing import Literal

from .presets import SYSTEM_PRESET_CATALOG


@dataclass(frozen=True, slots=True)
class MemoryScope:
    user_id: int
    system_preset_id: str


@dataclass(frozen=True, slots=True)
class MemorySource:
    kind: Literal["tool", "manual", "onboarding", "reflection", "interaction", "diary"]
    session_id: int | None = None
    batch_id: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingItem:
    id: int
    content: str
    version: int


def validate_memory_scope(scope: MemoryScope) -> None:
    if scope.user_id <= 0 or scope.system_preset_id not in SYSTEM_PRESET_CATALOG:
        raise ValueError("Invalid memory scope")
