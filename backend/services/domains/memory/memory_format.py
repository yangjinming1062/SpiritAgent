from components import DEFAULT_LANGUAGE
from modules.memory import Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts.memory import MemoryScope

from .memory_store import active_memory_filter, scope_filter

MAX_MEMORIES = 10


def _format_record(content: str, basis: str, context: str | None) -> str:
    return f"- [{basis}; {context or 'general'}] {content}"


async def format_memories_block(db: AsyncSession, scope: MemoryScope) -> str:
    rows = list(
        (
            await db.scalars(
                select(Memory)
                .where(
                    scope_filter(scope),
                    active_memory_filter(),
                    Memory.context.like("recall:%"),
                )
                .order_by(Memory.updated_at.desc())
                .limit(MAX_MEMORIES),
            )
        ).all(),
    )
    return "\n".join(_format_record(r.content, r.basis, r.context) for r in rows) or "（暂无有效长期记忆）"


async def format_background_memory_block(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    rows = list(
        (
            await db.scalars(
                select(Memory)
                .where(
                    scope_filter(scope),
                    active_memory_filter(),
                    Memory.usage == "background",
                    Memory.basis == "explicit",
                    Memory.context.like("recall:%"),
                )
                .order_by(Memory.id)
                .limit(MAX_MEMORIES),
            )
        ).all(),
    )
    if not rows:
        return ""
    title = (
        "# 用户明确表达的长期背景（按适用范围使用）"
        if language != "en"
        else "# Explicit enduring context (respect each claim's scope)"
    )
    return title + "\n" + "\n".join(_format_record(r.content, r.basis, r.context) for r in rows)


def format_proactive_memory_block(memories: list[dict], *, language: str = DEFAULT_LANGUAGE) -> str:
    if not memories:
        return ""
    title = (
        "# 相关有效记忆（推断不等于用户确认；尊重时效和范围）"
        if language != "en"
        else "# Relevant valid memories (inference is not user confirmation; respect scope and expiry)"
    )
    return (
        title
        + "\n"
        + "\n".join(_format_record(m["content"], m.get("basis", "system"), m.get("context")) for m in memories)
    )
