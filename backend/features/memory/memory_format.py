import re

from infrastructure.constants import DEFAULT_LANGUAGE
from infrastructure.text import resolve_language, resolve_prompt_text
from runtime.memory_scope import MemoryScope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.memory_narratives import load_companion_reflection, narrative_date
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

_SLOT_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")


def format_memory_record(
    content: str,
    basis: str,
    context: str | None,
    *,
    language: str = DEFAULT_LANGUAGE,
    local_date: str | None = None,
) -> str:
    """显示依据、话题或所属日期，隐藏内部命名空间。"""
    labels = MEMORY_BASIS_LABELS[resolve_language(language)]
    parts = [labels.get(basis, basis)]
    kind = next((kind for kind in ("diary", "reflection") if (context or "").startswith(f"{kind}:")), None)
    if kind is not None:
        parts.append(NARRATIVE_MEMORY_LABELS[resolve_language(language)][kind])
        if local_date:
            parts.append(local_date)
        elif slot_date := _SLOT_DATE_RE.search(context or ""):
            parts.append(slot_date.group())
    elif basis != "system":
        if topic := (context or "").removeprefix("recall:"):
            parts.append(topic)
    elif local_date:
        parts.append(local_date)
    elif slot_date := _SLOT_DATE_RE.search(context or ""):
        parts.append(slot_date.group())
    return f"- [{' · '.join(parts)}] {content}"


async def format_memories_block(db: AsyncSession, scope: MemoryScope, *, language: str = DEFAULT_LANGUAGE) -> str:
    rows = await db.scalars(
        select(Memory)
        .where(
            scope_filter(scope),
            active_memory_filter(),
            Memory.context.like("recall:%"),
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
                Memory.context.like("recall:%"),
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
            format_memory_record(m.content, m.basis, m.context, language=language, local_date=m.local_date)
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
            local_date=narrative_date(row.context, row.source_refs),
        )
    )
