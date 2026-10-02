"""伙伴日记的派生索引与最新相处理解。"""

import json
from datetime import date
from typing import Any

from components import session_scope
from modules.companion import CompanionDiaryEntry
from modules.memory import Memory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import EmbeddingItem, MemoryScope, MemorySource

from .memory_namespaces import KIND_TO_PREFIX, REFLECTION_CONTEXT
from .memory_store import (
    active_memory_filter,
    backfill_memory_embeddings,
    memory_write_lock,
    scope_filter,
    upsert_slotted_memory,
)


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


async def load_companion_reflection(db: AsyncSession, scope: MemoryScope) -> Memory | None:
    if scope.system_preset_id != "companion":
        return None
    return await db.scalar(
        select(Memory).where(scope_filter(scope), active_memory_filter(), Memory.context == REFLECTION_CONTEXT),
    )


async def save_companion_reflection(db: AsyncSession, scope: MemoryScope, content: str, source_date: date) -> Memory:
    if scope.system_preset_id != "companion":
        raise ValueError("Companion reflection belongs to the companion preset")
    await memory_write_lock(db, scope)
    previous = await load_companion_reflection(db, scope)
    if (
        previous is not None
        and (previous_date := narrative_date(previous.context, previous.source_refs)) is not None
        and previous_date >= source_date.isoformat()
    ):
        return previous
    return await upsert_slotted_memory(
        db,
        scope,
        REFLECTION_CONTEXT,
        content,
        json.dumps(["reflection"]),
        source=MemorySource("reflection", batch_id=source_date.isoformat()),
    )


async def index_diary_memory(db: AsyncSession, entry: CompanionDiaryEntry) -> Memory:
    scope = MemoryScope(entry.user_id, "companion")
    context = f"{KIND_TO_PREFIX['diary']}{entry.entry_date.isoformat()}"
    content = f"{entry.title}\n\n{entry.body}"
    await memory_write_lock(db, scope)
    existing = await db.scalar(select(Memory).where(scope_filter(scope), Memory.context == context))
    if existing is not None and existing.status == "active" and existing.content == content:
        return existing
    return await upsert_slotted_memory(
        db,
        scope,
        context,
        content,
        json.dumps(["diary"]),
        source=MemorySource("diary", batch_id=entry.entry_date.isoformat()),
    )


async def rebuild_diary_indexes(db: AsyncSession, user_id: int) -> None:
    entries = (await db.scalars(select(CompanionDiaryEntry).where(CompanionDiaryEntry.user_id == user_id))).all()
    for entry in entries:
        await index_diary_memory(db, entry)


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
