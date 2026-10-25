"""人工记忆管理的账户用例，解析完成后传入明确的记忆作用域。"""

from collections.abc import Sequence
from datetime import datetime

from infrastructure.database import SESSION_LOCAL
from infrastructure.pagination import decode_timestamp_cursor, encode_timestamp_cursor
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.admin_contracts import MemoryListResponse
from features.memory.contracts import MemoryDetail, SystemMemoryWrite
from features.memory.memory_admin import (
    list_memories,
    memory_counts,
    memory_detail,
)
from features.memory.memory_store import (
    backfill_memory_embeddings,
    create_memory,
    delete_memory,
    source_date,
    validate_source,
)
from features.memory.models import Memory


async def list_memory_view(
    scope: MemoryScope,
    *,
    kind: str | None,
    status: str,
    tag: str | None,
    query: str | None,
    limit: int,
    cursor: str | None = None,
    include_total: bool = False,
) -> MemoryListResponse:
    before = decode_timestamp_cursor(cursor)
    async with SESSION_LOCAL() as db:
        rows = await list_memories(
            db, scope, kind=kind, status=status, tag=tag, q=query, limit=limit + 1, before=before
        )
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            encode_timestamp_cursor(datetime.fromisoformat(rows[-1].updated_at), rows[-1].id) if more else None
        )
        counts = await memory_counts(db, scope, include_total=include_total, kind=kind, status=status, tag=tag, q=query)
        total = counts.pop("total", None)
        return MemoryListResponse(
            memories=rows,
            counts=counts,
            total=total,
            has_more=more,
            next_cursor=next_cursor,
            system_preset_id=scope.system_preset_id,
        )


async def forget_manual_memory(scope: MemoryScope, memory_id: int) -> bool:
    async with SESSION_LOCAL() as db:
        return await delete_memory(db, scope, memory_id)


async def remember_manual(
    scope: MemoryScope,
    session_id: int,
    content: str,
    context: str,
    tags: list[str],
    importance: float,
) -> EmbeddingItem:
    async with SESSION_LOCAL() as db:
        row = await create_memory(
            db,
            scope,
            source=MemorySource("manual", session_id=session_id),
            content=content,
            context=context,
            tags=tags,
            importance=importance,
        )
        await db.commit()
        result = EmbeddingItem(row.id, row.content, row.content_version)
    await backfill_memory_embeddings(scope, [result])
    return result


async def ensure_system_memories_in_session(
    db: AsyncSession, scope: MemoryScope, writes: Sequence[SystemMemoryWrite]
) -> None:
    if not writes:
        return
    if len({item.slot_key for item in writes}) != len(writes):
        raise ValueError("Duplicate system memory slots in one batch")
    for source in {item.source for item in writes}:
        await validate_source(db, scope, source)
    if any(
        not item.slot_key or (item.source_event_id is not None and scope.system_preset_id != "companion")
        for item in writes
    ):
        raise ValueError("Invalid system memory source or slot")
    # 系统事实不参加用户事实审阅；唯一槽位保留已存在、已修改或已遗忘的记录。
    for start in range(0, len(writes), 500):
        values = [
            {
                "user_id": scope.user_id,
                "system_preset_id": scope.system_preset_id,
                "kind": "recall",
                "slot_key": item.slot_key,
                "content": item.content,
                "context": item.context,
                "tags": list(item.tags),
                "basis": "system",
                "source_kind": item.source.kind,
                "source_conversation_id": item.source.session_id,
                "source_event_id": item.source_event_id,
                "source_batch_id": item.source.batch_id,
                "local_date": source_date(item.source),
            }
            for item in writes[start : start + 500]
        ]
        await db.execute(insert(Memory).values(values).on_conflict_do_nothing())


async def get_memory_detail(scope: MemoryScope, memory_id: int) -> MemoryDetail | None:
    async with SESSION_LOCAL() as db:
        return await memory_detail(db, scope, memory_id)
