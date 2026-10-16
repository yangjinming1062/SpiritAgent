"""人工记忆管理的账户用例，解析完成后传入明确的记忆作用域。"""

from dataclasses import dataclass

from infrastructure.database import SESSION_LOCAL
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource, validate_memory_scope

from features.conversation.memory_scope import resolve_memory_scope
from features.memory.memory_admin import (
    MemoryListItem,
    list_memories,
    memory_counts,
    memory_list_total,
    update_memory,
    update_user_profile,
)
from features.memory.memory_store import backfill_memory_embeddings, create_memory, delete_memory
from features.memory.profile_fields import MemoryProfileChange


@dataclass(frozen=True, slots=True)
class MemoryListView:
    memories: list[MemoryListItem]
    counts: dict[str, int]
    total: int


async def resolve_scope(user_id: int, session_id: str | None, preset_id: str | None) -> MemoryScope:
    if session_id is not None:
        async with SESSION_LOCAL() as db:
            return await resolve_memory_scope(db, user_id, session_id)
    if preset_id is None:
        raise ValueError("session_id or system_preset_id is required")
    scope = MemoryScope(user_id, preset_id)
    validate_memory_scope(scope)
    return scope


async def list_memory_view(
    scope: MemoryScope,
    *,
    kind: str | None,
    status: str,
    tag: str | None,
    query: str | None,
    limit: int,
    offset: int = 0,
) -> MemoryListView:
    async with SESSION_LOCAL() as db:
        rows = await list_memories(db, scope, kind=kind, status=status, tag=tag, q=query, limit=limit, offset=offset)
        total = await memory_list_total(db, scope, kind=kind, status=status, tag=tag, q=query)
        return MemoryListView(rows, await memory_counts(db, scope), total)


async def update_profile(scope: MemoryScope, changes: list[MemoryProfileChange]) -> list[MemoryListItem]:
    return await update_user_profile(scope, changes)


async def update_manual_memory(
    scope: MemoryScope,
    memory_id: int,
    content: str,
    expected_version: int,
) -> MemoryListItem | None:
    return await update_memory(scope, memory_id, content=content, expected_version=expected_version)


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
