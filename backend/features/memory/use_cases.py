"""人工记忆管理的账户用例，解析完成后传入明确的记忆作用域。"""

from datetime import datetime

from infrastructure.database import SESSION_LOCAL
from infrastructure.pagination import decode_timestamp_cursor, encode_timestamp_cursor
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource

from features.memory.admin_contracts import MemoryListResponse
from features.memory.contracts import MemoryDetail
from features.memory.memory_admin import list_memories, memory_counts, memory_detail
from features.memory.memory_store import backfill_memory_embeddings, create_memory, delete_memory


async def list_memory_view(
    scope: MemoryScope, *, kind: str | None, status: str, query: str | None, limit: int, cursor: str | None = None
) -> MemoryListResponse:
    before = decode_timestamp_cursor(cursor)
    async with SESSION_LOCAL() as db:
        rows = await list_memories(db, scope, kind=kind, status=status, q=query, limit=limit + 1, before=before)
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            encode_timestamp_cursor(datetime.fromisoformat(rows[-1].updated_at), rows[-1].id) if more else None
        )
        counts = await memory_counts(db, scope, kind=kind, status=status, q=query)
        return MemoryListResponse(
            memories=rows,
            counts=counts,
            has_more=more,
            next_cursor=next_cursor,
            system_preset_id=scope.system_preset_id,
        )


async def forget_manual_memory(scope: MemoryScope, memory_id: int) -> bool:
    async with SESSION_LOCAL() as db:
        return await delete_memory(db, scope, memory_id)


async def remember_manual(
    scope: MemoryScope, session_id: int, content: str, context: str, tags: list[str], importance: float
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


async def get_memory_detail(scope: MemoryScope, memory_id: int) -> MemoryDetail | None:
    async with SESSION_LOCAL() as db:
        return await memory_detail(db, scope, memory_id)
