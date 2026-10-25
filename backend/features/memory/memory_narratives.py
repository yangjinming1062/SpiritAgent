"""伙伴日记的派生索引与最新相处理解。"""

from collections.abc import Collection
from datetime import date

from infrastructure.database import session_scope
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import DiaryIndexSource, NarrativeMemorySnapshot
from .memory_store import (
    MemorySlotWrite,
    active_memory_filter,
    backfill_memory_embeddings,
    memory_write_lock,
    scope_filter,
    upsert_memory_slots,
    upsert_slotted_memory,
)
from .models import Memory, MemoryEmbeddingRow


async def _load_companion_reflection(db: AsyncSession, scope: MemoryScope) -> Memory | None:
    if scope.system_preset_id != "companion":
        return None
    return await db.scalar(
        select(Memory).where(
            scope_filter(scope), active_memory_filter(), Memory.kind == "reflection", Memory.slot_key == "current"
        )
    )


def _snapshot(row: Memory) -> NarrativeMemorySnapshot:
    return NarrativeMemorySnapshot(
        row.id,
        row.content,
        row.content_version,
        row.basis,
        row.context,
        row.local_date.isoformat() if row.local_date else None,
    )


async def load_companion_reflection(db: AsyncSession, scope: MemoryScope) -> NarrativeMemorySnapshot | None:
    row = await _load_companion_reflection(db, scope)
    return _snapshot(row) if row is not None else None


async def save_companion_reflection(
    db: AsyncSession, scope: MemoryScope, content: str, source_date: date
) -> NarrativeMemorySnapshot:
    if scope.system_preset_id != "companion":
        raise ValueError("Companion reflection belongs to the companion preset")
    await memory_write_lock(db, scope)
    previous = await _load_companion_reflection(db, scope)
    if previous is not None and previous.local_date is not None and previous.local_date >= source_date:
        return _snapshot(previous)
    row = await upsert_slotted_memory(
        db,
        scope,
        "current",
        content,
        ["reflection"],
        kind="reflection",
        source=MemorySource("reflection", batch_id=source_date.isoformat()),
    )
    return _snapshot(row)


async def index_diary_memory(db: AsyncSession, entry: DiaryIndexSource) -> None:
    scope = MemoryScope(entry.user_id, "companion")
    key = entry.entry_date.isoformat()
    content = f"{entry.title}\n\n{entry.body}"
    await memory_write_lock(db, scope)
    existing = await db.scalar(
        select(Memory).where(scope_filter(scope), Memory.kind == "diary", Memory.slot_key == key)
    )
    if existing is not None and existing.status == "active" and existing.content == content:
        return
    await upsert_slotted_memory(
        db,
        scope,
        key,
        content,
        ["diary"],
        kind="diary",
        context=entry.title,
        source=MemorySource("diary", batch_id=key),
    )


async def backfill_diary_embeddings(user_id: int) -> None:
    scope = MemoryScope(user_id, "companion")
    async with session_scope() as db:
        current = exists(
            select(MemoryEmbeddingRow.id).where(
                MemoryEmbeddingRow.memory_id == Memory.id, MemoryEmbeddingRow.content_version == Memory.content_version
            )
        )
        rows = await db.scalars(
            select(Memory).where(scope_filter(scope), active_memory_filter(), Memory.kind == "diary", ~current)
        )
        items = [EmbeddingItem(row.id, row.content, row.content_version) for row in rows]
    await backfill_memory_embeddings(scope, items)


async def index_diary_memories(db: AsyncSession, entries: list[DiaryIndexSource]) -> None:
    if not entries:
        return
    user_id = entries[0].user_id
    if any(entry.user_id != user_id for entry in entries):
        raise ValueError("A diary index batch must belong to one account")
    await upsert_memory_slots(
        db,
        MemoryScope(user_id, "companion"),
        [
            MemorySlotWrite(
                kind="diary",
                slot_key=entry.entry_date.isoformat(),
                content=f"{entry.title}\n\n{entry.body}",
                tags=["diary"],
                source=MemorySource("diary", batch_id=entry.entry_date.isoformat()),
                context=entry.title,
            )
            for entry in entries
        ],
    )


async def remove_absent_diary_indexes(db: AsyncSession, user_id: int, dates: Collection[date]) -> None:
    conditions = [Memory.user_id == user_id, Memory.system_preset_id == "companion", Memory.kind == "diary"]
    if dates:
        conditions.append(or_(Memory.local_date.not_in(dates), Memory.local_date.is_(None)))
    await db.execute(delete(Memory).where(*conditions))
