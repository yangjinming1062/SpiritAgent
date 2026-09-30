from typing import get_args

from modules.memory import Memory
from sqlalchemy import ColumnElement, or_

from .memory_policy import MemoryCategory

RECALL_TAGS: frozenset[str] = frozenset(get_args(MemoryCategory))
KIND_TO_PREFIX: dict[str, str] = {
    "recall": "recall:",
    "user_profile": "user_profile:",
    "interaction_stats": "interaction_stats:",
    "diary": "diary:",
}
# 不参与检索召回：用户资料由专门的块注入，统计不是记忆；夜间反思（diary:）写给后续回忆，参与召回。
RESERVED_FROM_RECALL: frozenset[str] = frozenset(
    {KIND_TO_PREFIX["user_profile"], KIND_TO_PREFIX["interaction_stats"]},
)
_RECALL_LABEL_MAX = 200


def context_not_in(prefix: str) -> ColumnElement[bool]:
    return or_(Memory.context.is_(None), ~Memory.context.like(f"{prefix}%"))


def participates_in_recall(context: str | None) -> bool:
    return context is None or not any(context.startswith(prefix) for prefix in RESERVED_FROM_RECALL)


def normalize_recall_context(raw: str | None) -> str:
    label = (raw or "").strip() or "general"
    return label if label.startswith("recall:") else f"recall:{label[:_RECALL_LABEL_MAX]}"
