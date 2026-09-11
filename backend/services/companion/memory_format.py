from components import DEFAULT_LANGUAGE, resolve_prompt_text
from modules.memory import Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .memory_namespaces import (
    AUTO_INJECT_SLOTS,
    INFERRED_PROFILE_SLOTS,
    KIND_TO_PREFIX,
    STATIC_BLOCK_EXCLUDED,
    context_not_in,
)

MAX_MEMORIES = 10
MAX_MEMORY_SNIPPET_LEN = 200

# 双语提示词块标签：auto_inject / inferred_profile / proactive_memory 三段开头的标题。
# zh 为直译占位，en 保留重构前英文原值便于回滚 1:1 对照。
# 槽位 display (slot_name = context.split(":",1)[1].replace("_"," ").capitalize()) 是协议级展示，保持英文不译。

_AUTO_INJECT_LABELS_TEXTS: dict[str, str] = {
    "zh": "# 自动注入记忆（始终生效）",
    "en": "# Active auto-inject memories (always in effect)",
}

_INFERRED_PROFILE_LABELS_TEXTS: dict[str, str] = {
    "zh": "# 推断出的用户资料（背景知识）",
    "en": "# Inferred user profile (background knowledge)",
}

_PROACTIVE_MEMORY_LABELS_TEXTS: dict[str, str] = {
    "zh": "# 相关的长期记忆（主动检索到、用于当前上下文）",
    "en": "# Relevant long-term memories (proactively retrieved for current context)",
}


async def format_memories_block(db: AsyncSession, user_id: int) -> str:
    """以列表形式渲染用户最近的长期记忆；有独立提示词槽位或检索路径的命名空间已排除，无记忆时返回占位文案供提示词直接插值。"""
    rows = (
        (
            await db.execute(
                select(Memory)
                .where(Memory.user_id == user_id, *[context_not_in(p) for p in STATIC_BLOCK_EXCLUDED])
                .order_by(Memory.updated_at.desc())
                .limit(MAX_MEMORIES),
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return "（暂无长期记忆）"
    lines = []
    for r in rows:
        snippet = (r.content or "")[:MAX_MEMORY_SNIPPET_LEN]
        ctx = f" [{r.context}]" if r.context else ""
        lines.append(f"- {snippet}{ctx}")
    return "\n".join(lines)


async def format_auto_inject_block(db: AsyncSession, user_id: int, *, language: str = DEFAULT_LANGUAGE) -> str:
    rows = (
        (
            await db.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.context.like(KIND_TO_PREFIX["auto_inject"] + "%"),
                ),
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ""
    by_slot = {r.context: r for r in rows}
    ordered = [by_slot[s] for s in AUTO_INJECT_SLOTS if s in by_slot]
    lines = [resolve_prompt_text(_AUTO_INJECT_LABELS_TEXTS, language)]
    for r in ordered:
        slot_name = r.context.split(":", 1)[1].replace("_", " ")
        lines.append(f"- **{slot_name}**: {r.content}")
    return "\n".join(lines)


async def format_inferred_profile_block(db: AsyncSession, user_id: int, *, language: str = DEFAULT_LANGUAGE) -> str:
    rows = (
        (
            await db.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.context.like(KIND_TO_PREFIX["inferred_profile"] + "%"),
                ),
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ""
    by_slot = {r.context: r for r in rows}
    ordered = [by_slot[s] for s in INFERRED_PROFILE_SLOTS if s in by_slot]
    if not ordered:
        return ""
    lines = [resolve_prompt_text(_INFERRED_PROFILE_LABELS_TEXTS, language)]
    for r in ordered:
        slot_name = r.context.split(":", 1)[1].replace("_", " ")
        lines.append(f"- **{slot_name}**: {r.content}")
    return "\n".join(lines)


def format_proactive_memory_block(memories: list[dict], *, language: str = DEFAULT_LANGUAGE) -> str:
    if not memories:
        return ""
    lines = [resolve_prompt_text(_PROACTIVE_MEMORY_LABELS_TEXTS, language)]
    for m in memories:
        ctx = f" [{m['context']}]" if m.get("context") else ""
        content = (m.get("content") or "").strip()
        lines.append(f"- {content}{ctx}")
    return "\n".join(lines)
