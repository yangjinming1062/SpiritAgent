"""伙伴对双方关系和相处方式的最新理解。"""

from datetime import date

from runtime.memory_scope import MemoryScope, MemorySource
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import NarrativeMemorySnapshot
from .memory_store import active_memory_filter, memory_write_lock, scope_filter, upsert_slotted_memory
from .models import Memory


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
