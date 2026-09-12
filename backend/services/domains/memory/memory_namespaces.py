from dataclasses import dataclass

from modules.memory import Memory
from sqlalchemy import ColumnElement, or_

# 封闭分类。LLM 必须从这些列表中选取标签，自由形式标签在写入时被拒绝，确保不同会话/供应商间词汇稳定。
# 分类原则：按事实「在对话中如何呈现」划分——auto_inject 是每次对话的背景上下文（两人对话时先天就在的背景），recall 是按需检索的零散事实（需要时调出来用的小事实）。

RECALL_TAGS: frozenset[str] = frozenset(
    {
        "user_preference",
        "likes",
        "dislikes",
        "key_constraints",
        "other",
        "tool_quirk",
        "environment",
    },
)

AUTO_INJECT_SLOTS: tuple[str, ...] = (
    "auto_inject:communication_style",
    "auto_inject:rapport_state",
    "auto_inject:interaction_pattern",
    "auto_inject:mood_pattern",
    "auto_inject:relationship_signal",
)

INFERRED_PROFILE_SLOTS: tuple[str, ...] = (
    "inferred_profile:basic_info",
    "inferred_profile:work_schedule",
    "inferred_profile:interests",
    "inferred_profile:preferences",
    "inferred_profile:important_dates",
    "inferred_profile:relationships",
    "inferred_profile:goals_stressors",
    "inferred_profile:freeform",
)


@dataclass(frozen=True)
class NamespaceSpec:
    name: str
    prefix: str
    forbidden_from_llm: bool = False
    reserved_from_recall: bool = False
    excluded_from_static_block: bool = False
    slots: tuple[str, ...] | None = None


NAMESPACE_SPECS: dict[str, NamespaceSpec] = {
    "recall": NamespaceSpec("recall", "recall:"),
    "auto_inject": NamespaceSpec(
        "auto_inject",
        "auto_inject:",
        reserved_from_recall=True,
        excluded_from_static_block=True,
        slots=AUTO_INJECT_SLOTS,
    ),
    "user_profile": NamespaceSpec(
        "user_profile",
        "user_profile:",
        forbidden_from_llm=True,
        reserved_from_recall=True,
        excluded_from_static_block=True,
    ),
    "interaction_stats": NamespaceSpec(
        "interaction_stats",
        "interaction_stats:",
        forbidden_from_llm=True,
        reserved_from_recall=True,
        excluded_from_static_block=True,
    ),
    "inferred_profile": NamespaceSpec(
        "inferred_profile",
        "inferred_profile:",
        forbidden_from_llm=True,
        reserved_from_recall=True,
        excluded_from_static_block=True,
        slots=INFERRED_PROFILE_SLOTS,
    ),
    "diary": NamespaceSpec("diary", "diary:", forbidden_from_llm=True, excluded_from_static_block=True),
}

KIND_TO_PREFIX: dict[str, str] = {s.name: s.prefix for s in NAMESPACE_SPECS.values()}
FORBIDDEN_FROM_LLM: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.forbidden_from_llm)
RESERVED_FROM_RECALL: frozenset[str] = frozenset(s.prefix for s in NAMESPACE_SPECS.values() if s.reserved_from_recall)
STATIC_BLOCK_EXCLUDED: frozenset[str] = frozenset(
    s.prefix for s in NAMESPACE_SPECS.values() if s.excluded_from_static_block
)

_RECALL_LABEL_MAX = 200
_RECALL_TAG_FALLBACK = "other"


def context_not_in(prefix: str) -> ColumnElement[bool]:
    """SQL 谓词：``context IS NULL OR context NOT LIKE '<prefix>%'``——NULL 上下文行在纯 ``~like`` 下会被三值逻辑吞掉，调用方都需要这种 NULL 豁免形式。"""
    return or_(Memory.context.is_(None), ~Memory.context.like(f"{prefix}%"))


def participates_in_recall(context: str | None) -> bool:
    """Python 侧判定 context 是否参与 recall 检索（与检索 SQL 的 context_not_in 谓词同口径）；不参与的命名空间写库时跳过向量生成。"""
    return context is None or not any(context.startswith(prefix) for prefix in RESERVED_FROM_RECALL)


def normalize_recall_context(raw: str | None, *, default: str = "general") -> str:
    """裁剪、缺省、补齐 recall 行的 context 前缀；LLM 写入与 consolidator 共用，确保 ``recall:`` 命名空间在两端被强制一致。"""
    label = (raw or "").strip() or default
    if not label.startswith(KIND_TO_PREFIX["recall"]):
        label = f"{KIND_TO_PREFIX['recall']}{label[:_RECALL_LABEL_MAX]}"
    return label


def normalize_recall_tags(raw: list | None) -> list[str]:
    """过滤 LLM 给出的标签，仅保留 ``RECALL_TAGS`` 集合内的项；空结果回退为 'other'。"""
    cleaned = [t for t in (raw or []) if t in RECALL_TAGS]
    return cleaned or [_RECALL_TAG_FALLBACK]
