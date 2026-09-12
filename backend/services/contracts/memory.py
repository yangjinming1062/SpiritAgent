from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class MemoryScope:
    user_id: int
    system_preset_id: str


@dataclass(frozen=True, slots=True)
class MemorySource:
    kind: Literal["tool", "manual", "onboarding", "reflection", "consolidation", "interaction", "diary"]
    session_id: int | None = None
    message_ids: tuple[int, ...] = ()
    batch_id: str | None = None
    memory_versions: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class EmbeddingItem:
    id: int
    content: str
    version: int
