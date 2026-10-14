"""伙伴日记的派生索引与最新相处理解。"""

import json
from copy import deepcopy
from datetime import date
from typing import Any

from infrastructure.database import session_scope
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.contracts import DiaryIndexSource, NarrativeMemorySnapshot
from features.memory.memory_namespaces import KIND_TO_PREFIX, REFLECTION_CONTEXT
from features.memory.memory_store import (
    active_memory_filter,
    backfill_memory_embeddings,
    memory_write_lock,
    scope_filter,
    upsert_slotted_memory,
)
from features.memory.models import Memory


def narrative_date(context: str | None, refs: dict[str, Any]) -> str | None:
    if context and context.startswith(KIND_TO_PREFIX["diary"]):
        raw = context.removeprefix(KIND_TO_PREFIX["diary"])
    else:
        original = refs.get("original_source", refs)
        raw = original.get("batch_id") if isinstance(original, dict) else None
    try:
        return date.fromisoformat(raw).isoformat() if isinstance(raw, str) else None
    except ValueError:
        return None


async def _load_companion_reflection(db: AsyncSession, scope: MemoryScope) -> Memory | None:
    if scope.system_preset_id != "companion":
        return None
    return await db.scalar(
        select(Memory).where(scope_filter(scope), active_memory_filter(), Memory.context == REFLECTION_CONTEXT),
    )


def _snapshot(row: Memory) -> NarrativeMemorySnapshot:
    return NarrativeMemorySnapshot(
        row.id,
        row.content,
        row.content_version,
        row.basis,
        row.context,
        deepcopy(row.source_refs),
    )


async def load_companion_reflection(db: AsyncSession, scope: MemoryScope) -> NarrativeMemorySnapshot | None:
    row = await _load_companion_reflection(db, scope)
    return _snapshot(row) if row is not None else None


async def save_companion_reflection(
    db: AsyncSession,
    scope: MemoryScope,
    content: str,
    source_date: date,
) -> NarrativeMemorySnapshot:
    if scope.system_preset_id != "companion":
        raise ValueError("Companion reflection belongs to the companion preset")
    await memory_write_lock(db, scope)
    previous = await _load_companion_reflection(db, scope)
    if (
        previous is not None
        and (previous_date := narrative_date(previous.context, previous.source_refs)) is not None
        and previous_date >= source_date.isoformat()
    ):
        return _snapshot(previous)
    row = await upsert_slotted_memory(
        db,
        scope,
        REFLECTION_CONTEXT,
        content,
        json.dumps(["reflection"]),
        source=MemorySource("reflection", batch_id=source_date.isoformat()),
    )
    return _snapshot(row)


async def index_diary_memory(db: AsyncSession, entry: DiaryIndexSource) -> None:
    scope = MemoryScope(entry.user_id, "companion")
    context = f"{KIND_TO_PREFIX['diary']}{entry.entry_date.isoformat()}"
    content = f"{entry.title}\n\n{entry.body}"
    await memory_write_lock(db, scope)
    existing = await db.scalar(select(Memory).where(scope_filter(scope), Memory.context == context))
    if existing is not None and existing.status == "active" and existing.content == content:
        return
    await upsert_slotted_memory(
        db,
        scope,
        context,
        content,
        json.dumps(["diary"]),
        source=MemorySource("diary", batch_id=entry.entry_date.isoformat()),
    )


async def backfill_diary_embeddings(user_id: int) -> None:
    scope = MemoryScope(user_id, "companion")
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Memory).where(
                    scope_filter(scope),
                    active_memory_filter(),
                    Memory.context.like("diary:%"),
                    Memory.embedding.is_(None),
                ),
            )
        ).all()
        items = [EmbeddingItem(row.id, row.content, row.content_version) for row in rows]
    await backfill_memory_embeddings(scope, items)
