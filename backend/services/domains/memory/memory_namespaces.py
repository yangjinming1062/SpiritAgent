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
RESERVED_FROM_RECALL: frozenset[str] = frozenset(prefix for kind, prefix in KIND_TO_PREFIX.items() if kind != "recall")
_RECALL_LABEL_MAX = 200


def context_not_in(prefix: str) -> ColumnElement[bool]:
    return or_(Memory.context.is_(None), ~Memory.context.like(f"{prefix}%"))


def participates_in_recall(context: str | None) -> bool:
    return context is None or not any(context.startswith(prefix) for prefix in RESERVED_FROM_RECALL)


def normalize_recall_context(raw: str | None, *, default: str = "general") -> str:
    label = (raw or "").strip() or default
    return label if label.startswith("recall:") else f"recall:{label[:_RECALL_LABEL_MAX]}"
