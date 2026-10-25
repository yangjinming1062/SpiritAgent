"""批量读取证据并保存必要修订；列表只读取当前内容。"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import MemoryEvidence, MemoryListItem, MemoryRecord, MemorySummary
from .models import (
    MEMORY_REVISION_LIMIT,
    Memory,
    MemoryEmbeddingRow,
    MemoryEvidenceRow,
    MemoryFingerprint,
    MemoryRevision,
)


def memory_summary(row: Memory) -> MemorySummary:
    return MemorySummary(
        id=row.id,
        content_version=row.content_version,
        content=row.content,
        context=row.context,
        kind=row.kind,
        slot_key=row.slot_key,
        category=row.category,
        tags=row.tags,
        basis=row.basis,
        status=row.status,
        usage=row.usage,
        reason=row.reason,
        source_kind=row.source_kind,
        updated_at=row.updated_at.isoformat(),
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
    )


def memory_list_item(row: Memory) -> MemoryListItem:
    return MemoryListItem(
        **memory_summary(row).model_dump(),
        system_preset_id=row.system_preset_id,
        importance=row.importance,
        created_at=row.created_at.isoformat(),
    )


def memory_record(row: Memory, evidence: Sequence[MemoryEvidence] = ()) -> MemoryRecord:
    return MemoryRecord(**memory_summary(row).model_dump(), evidence=list(evidence))


async def load_evidence(db: AsyncSession, memory_ids: Sequence[int]) -> dict[int, list[MemoryEvidence]]:
    result: dict[int, list[MemoryEvidence]] = {key: [] for key in memory_ids}
    if not memory_ids:
        return result
    rows = await db.scalars(
        select(MemoryEvidenceRow).where(MemoryEvidenceRow.memory_id.in_(memory_ids)).order_by(MemoryEvidenceRow.id)
    )
    for row in rows:
        result[row.memory_id].append(
            MemoryEvidence(
                fingerprint=row.fingerprint,
                message_id=row.message_id,
                quote=row.quote,
                stance=row.stance,
                created_at=row.source_created_at.isoformat(),
                source_unavailable=True if row.message_id is None else None,
            )
        )
    return result


async def replace_evidence(
    db: AsyncSession, memories: Sequence[Memory], evidence: dict[int, list[MemoryEvidence]]
) -> None:
    ids = [row.id for row in memories]
    if not ids:
        return
    await db.execute(delete(MemoryEvidenceRow).where(MemoryEvidenceRow.memory_id.in_(ids)))
    rows = []
    fingerprints = []
    for memory in memories:
        for item in evidence.get(memory.id, []):
            if item.quote is None or item.stance is None or item.created_at is None:
                raise ValueError("Original memory evidence is incomplete")
            rows.append(
                {
                    "user_id": memory.user_id,
                    "memory_id": memory.id,
                    "message_id": item.message_id,
                    "fingerprint": item.fingerprint,
                    "quote": item.quote,
                    "stance": item.stance,
                    "source_created_at": datetime.fromisoformat(item.created_at),
                }
            )
            fingerprints.append(
                {
                    "user_id": memory.user_id,
                    "system_preset_id": memory.system_preset_id,
                    "memory_id": memory.id,
                    "fingerprint": item.fingerprint,
                    "suppressed": False,
                }
            )
    if rows:
        await db.execute(insert(MemoryEvidenceRow), rows)
        await db.execute(
            insert(MemoryFingerprint)
            .values(fingerprints)
            .on_conflict_do_nothing(constraint="uq_memory_fingerprint_source")
        )


async def save_revisions(db: AsyncSession, revisions: list[dict[str, Any]]) -> None:
    if not revisions:
        return
    await db.execute(
        insert(MemoryRevision).values(revisions).on_conflict_do_nothing(constraint="uq_memory_revision_version")
    )
    ids = {revision["memory_id"] for revision in revisions}
    ranked = (
        select(
            MemoryRevision.id,
            func.row_number()
            .over(partition_by=MemoryRevision.memory_id, order_by=MemoryRevision.content_version.desc())
            .label("position"),
        )
        .where(MemoryRevision.memory_id.in_(ids))
        .subquery()
    )
    await db.execute(
        delete(MemoryRevision).where(
            MemoryRevision.id.in_(select(ranked.c.id).where(ranked.c.position > MEMORY_REVISION_LIMIT))
        )
    )


async def clear_memory_details(
    db: AsyncSession, memory_ids: Sequence[int], *, suppress: bool, keep_revisions: bool = False
) -> None:
    if not memory_ids:
        return
    if suppress:
        await db.execute(
            update(MemoryFingerprint).where(MemoryFingerprint.memory_id.in_(memory_ids)).values(suppressed=True)
        )
    models = (
        (MemoryEvidenceRow, MemoryEmbeddingRow)
        if keep_revisions
        else (MemoryEvidenceRow, MemoryRevision, MemoryEmbeddingRow)
    )
    for model in models:
        await db.execute(delete(model).where(model.memory_id.in_(memory_ids)))
