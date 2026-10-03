import re

from components import DEFAULT_LANGUAGE, SETTINGS, resolve_language, resolve_prompt_text
from modules.memory import Memory
from prompts.memory import (
    BACKGROUND_MEMORY_LABELS_TEXTS,
    COMPANION_REFLECTION_LABELS,
    MEMORY_BASIS_LABELS,
    NARRATIVE_MEMORY_LABELS,
    PROACTIVE_MEMORY_LABELS_TEXTS,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope

from .memory_narratives import load_companion_reflection, narrative_date
from .memory_retrieval import MemoryRecallResult
from .memory_store import active_memory_filter, scope_filter

_SLOT_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")


def _format_record(content: str, basis: str, context: str | None, language: str, local_date: str | None = None) -> str:
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
                .limit(SETTINGS.memory_prompt_max_memories),
            )
        ).all(),
    )
    return "\n".join(_format_record(r.content, r.basis, r.context, language) for r in rows)


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
                .limit(SETTINGS.memory_prompt_max_memories),
            )
        ).all(),
    )
    if not rows:
        return ""
    return (
        resolve_prompt_text(BACKGROUND_MEMORY_LABELS_TEXTS, language)
        + "\n"
        + "\n".join(_format_record(r.content, r.basis, r.context, language) for r in rows)
    )


def format_proactive_memory_block(memories: list[MemoryRecallResult], *, language: str = DEFAULT_LANGUAGE) -> str:
    if not memories:
        return ""
    return (
        resolve_prompt_text(PROACTIVE_MEMORY_LABELS_TEXTS, language)
        + "\n"
        + "\n".join(_format_record(m.content, m.basis, m.context, language, m.local_date) for m in memories)
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
        + _format_record(row.content, row.basis, row.context, language, narrative_date(row.context, row.source_refs))
    )
