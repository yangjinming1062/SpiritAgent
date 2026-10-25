from infrastructure.constants import DEFAULT_LANGUAGE
from infrastructure.text import resolve_language, resolve_prompt_text
from runtime.memory_scope import MemoryScope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.memory_narratives import load_companion_reflection
from features.memory.memory_retrieval import MemoryRecallResult
from features.memory.memory_store import active_memory_filter, scope_filter
from features.memory.models import Memory
from features.memory.prompts import (
    BACKGROUND_MEMORY_LABELS_TEXTS,
    COMPANION_REFLECTION_LABELS,
    MEMORY_BASIS_LABELS,
    NARRATIVE_MEMORY_LABELS,
    PROACTIVE_MEMORY_LABELS_TEXTS,
)


def format_memory_record(
    content: str,
    basis: str,
    context: str | None,
    *,
    language: str = DEFAULT_LANGUAGE,
    local_date: str | None = None,
    kind: str = "recall",
) -> str:
    """显示依据、话题或所属日期，隐藏内部命名空间。"""
    labels = MEMORY_BASIS_LABELS[resolve_language(language)]
    parts = [labels.get(basis, basis)]
    if kind in {"diary", "reflection"}:
        parts.append(NARRATIVE_MEMORY_LABELS[resolve_language(language)][kind])
        if local_date:
            parts.append(local_date)
    elif basis != "system" and context:
        parts.append(context)
    elif local_date:
        parts.append(local_date)
    return f"- [{' · '.join(parts)}] {content}"


async def format_memories_block(db: AsyncSession, scope: MemoryScope, *, language: str = DEFAULT_LANGUAGE) -> str:
    rows = await db.scalars(
        select(Memory)
        .where(
            scope_filter(scope),
            active_memory_filter(),
            Memory.kind == "recall",
        )
        .order_by(Memory.updated_at.desc(), Memory.id),
    )
    return "\n".join(format_memory_record(r.content, r.basis, r.context, language=language) for r in rows)


async def format_background_memory_block(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    rows = (
        await db.scalars(
            select(Memory)
            .where(
                scope_filter(scope),
                active_memory_filter(),
                Memory.usage == "background",
                Memory.basis == "explicit",
                Memory.kind == "recall",
            )
            .order_by(Memory.id),
        )
    ).all()
    if not rows:
        return ""
    return (
        resolve_prompt_text(BACKGROUND_MEMORY_LABELS_TEXTS, language)
        + "\n"
        + "\n".join(format_memory_record(r.content, r.basis, r.context, language=language) for r in rows)
    )


def format_proactive_memory_block(memories: list[MemoryRecallResult], *, language: str = DEFAULT_LANGUAGE) -> str:
    if not memories:
        return ""
    return (
        resolve_prompt_text(PROACTIVE_MEMORY_LABELS_TEXTS, language)
        + "\n"
        + "\n".join(
            format_memory_record(m.content, m.basis, m.context, language=language, local_date=m.local_date, kind=m.kind)
            for m in memories
        )
    )


async def format_companion_reflection_block(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    row = await load_companion_reflection(db, scope)
    if row is None:
        return ""
    return (
        resolve_prompt_text(COMPANION_REFLECTION_LABELS, language)
        + "\n"
        + format_memory_record(
            row.content,
            row.basis,
            row.context,
            language=language,
            local_date=row.local_date,
            kind="reflection",
        )
    )
