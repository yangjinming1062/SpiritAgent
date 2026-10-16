"""人工记忆管理的账户用例，解析完成后传入明确的记忆作用域。"""

from infrastructure.database import SESSION_LOCAL
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource

from features.memory.admin_contracts import MemoryListResponse
from features.memory.memory_admin import (
    list_memories,
    memory_counts,
    memory_list_total,
)
from features.memory.memory_store import backfill_memory_embeddings, create_memory, delete_memory


async def list_memory_view(
    scope: MemoryScope,
    *,
    kind: str | None,
    status: str,
    tag: str | None,
    query: str | None,
    limit: int,
    offset: int = 0,
) -> MemoryListResponse:
    async with SESSION_LOCAL() as db:
        rows = await list_memories(db, scope, kind=kind, status=status, tag=tag, q=query, limit=limit, offset=offset)
        total = await memory_list_total(db, scope, kind=kind, status=status, tag=tag, q=query)
        return MemoryListResponse(
            memories=rows,
            counts=await memory_counts(db, scope),
            total=total,
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
    tags: str,
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
