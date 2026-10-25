from typing import Literal, get_args

from features.memory.memory_policy import MemoryCategory

MemoryKind = Literal["recall", "user_profile", "interaction_stats", "diary", "reflection"]
MEMORY_KINDS = frozenset(get_args(MemoryKind))
RECALL_TAGS: frozenset[str] = frozenset(get_args(MemoryCategory))
RESERVED_FROM_RECALL = frozenset({"user_profile", "interaction_stats"})


def participates_in_recall(kind: str) -> bool:
    return kind not in RESERVED_FROM_RECALL
