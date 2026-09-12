from dataclasses import dataclass
from datetime import datetime
from typing import Any

from components import get_logger, session_scope
from modules.conversation import Conversation, Message
from modules.memory import Memory
from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts.memory import EmbeddingItem, MemoryScope, MemorySource
from services.domains.conversation import validate_memory_scope
from services.infrastructure.llm import generate_embeddings, resolve_embedding_provider

logger = get_logger(__name__)
MEMORY_EMBEDDING_DIM = 1536
_SLOTTED_PREFIXES = (
    "auto_inject:",
    "user_profile:",
    "inferred_profile:",
    "diary:",
    "interaction_stats:",
    "recall:nightly_actions:",
)


def scope_filter(scope: MemoryScope) -> ColumnElement[bool]:
    validate_memory_scope(scope)
    return and_(Memory.user_id == scope.user_id, Memory.system_preset_id == scope.system_preset_id)


async def _source_refs(db: AsyncSession, scope: MemoryScope, source: MemorySource) -> dict[str, Any]:
    validate_memory_scope(scope)
    if source.kind not in {"tool", "manual", "onboarding", "reflection", "consolidation", "interaction", "diary"}:
        raise ValueError("Invalid memory source kind")
    refs: dict[str, Any] = {}
    if source.memory_versions:
        if source.kind != "consolidation" or len(source.memory_versions) > 1024:
            raise ValueError("Invalid consolidation provenance")
        refs["memory_versions"] = [[mid, version] for mid, version in source.memory_versions]
    if source.session_id is not None:
        conv = await db.scalar(
            select(Conversation).where(
                Conversation.id == source.session_id,
                Conversation.user_id == scope.user_id,
                Conversation.system_preset_id == scope.system_preset_id,
                Conversation.is_automation.is_(False),
            ),
        )
        if conv is None:
            raise ValueError("Memory source conversation not found")
        refs["session_id"] = conv.id
        if source.message_ids:
            found = set(
                (
                    await db.scalars(
                        select(Message.id).where(
                            Message.conversation_id == conv.id,
                            Message.id.in_(source.message_ids),
                            Message.id > conv.context_after_message_id,
                        ),
                    )
                ).all(),
            )
            if found != set(source.message_ids):
                raise ValueError("Invalid memory source messages")
            refs["message_ids"] = sorted(found)
    elif source.message_ids:
        raise ValueError("Source messages require a conversation")
    if source.batch_id is not None:
        if len(source.batch_id) > 128:
            raise ValueError("Memory source batch is too long")
        refs["batch_id"] = source.batch_id
    return refs


async def create_memory(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    content: str,
    context: str,
    tags: str,
    source: MemorySource,
    importance: float = 1.0,
) -> Memory:
    refs = await _source_refs(db, scope, source)
    row = Memory(
        user_id=scope.user_id,
        system_preset_id=scope.system_preset_id,
        content=content,
        context=context,
        tags=tags,
        importance=importance,
        source_kind=source.kind,
        source_refs=refs,
    )
    db.add(row)
    await db.flush()
    return row


async def get_memory(db: AsyncSession, scope: MemoryScope, memory_id: int) -> Memory | None:
    return await db.scalar(select(Memory).where(scope_filter(scope), Memory.id == memory_id))


async def upsert_slotted_memory(
    db: AsyncSession,
    scope: MemoryScope,
    context: str,
    content: str,
    tags: str,
    *,
    source: MemorySource,
) -> Memory:
    refs = await _source_refs(db, scope, source)
    prefix = next((prefix for prefix in _SLOTTED_PREFIXES if context.startswith(prefix)), None)
    if prefix is None:
        raise ValueError("Context is not a unique memory slot")
    changed = Memory.content.is_distinct_from(content)
    stmt = (
        insert(Memory)
        .values(
            user_id=scope.user_id,
            system_preset_id=scope.system_preset_id,
            content=content,
            context=context,
            tags=tags,
            source_kind=source.kind,
            source_refs=refs,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "system_preset_id", "context"],
            index_where=text(f"context LIKE '{prefix}%'"),
            set_={
                "content": content,
                "tags": tags,
                "source_kind": source.kind,
                "source_refs": refs,
                "content_version": Memory.content_version + case((changed, 1), else_=0),
                "embedding": case((changed, None), else_=Memory.embedding),
                "updated_at": func.now(),
            },
        )
        .returning(Memory)
    )
    return (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one()


async def update_memory_content(db: AsyncSession, scope: MemoryScope, memory_id: int, content: str) -> Memory | None:
    changed = Memory.content.is_distinct_from(content)
    return (
        await db.execute(
            update(Memory)
            .where(scope_filter(scope), Memory.id == memory_id)
            .values(
                content=content,
                content_version=Memory.content_version + case((changed, 1), else_=0),
                embedding=case((changed, None), else_=Memory.embedding),
                source_kind="manual",
                source_refs={},
                updated_at=func.now(),
            )
            .returning(Memory)
            .execution_options(populate_existing=True),
        )
    ).scalar_one_or_none()


async def delete_memory(db: AsyncSession, scope: MemoryScope, memory_id: int) -> bool:
    deleted = await db.scalar(delete(Memory).where(scope_filter(scope), Memory.id == memory_id).returning(Memory.id))
    await db.commit()
    return deleted is not None


@dataclass(frozen=True, slots=True)
class RecallSnapshot:
    id: int
    content: str
    context: str | None
    tags: str | None
    importance: float
    updated_at: datetime
    version: int

    def prompt_row(self) -> dict[str, int | str | None]:
        return {"id": self.id, "context": self.context, "tags": self.tags, "content": self.content}


async def load_recall_snapshot(db: AsyncSession, scope: MemoryScope, *, limit: int) -> list[RecallSnapshot]:
    rows = (
        await db.scalars(
            select(Memory)
            .where(scope_filter(scope), Memory.context.like("recall:%"))
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(limit),
        )
    ).all()
    return [
        RecallSnapshot(
            row.id,
            row.content,
            row.context,
            row.tags,
            float(row.importance),
            row.updated_at,
            row.content_version,
        )
        for row in rows
    ]


async def replace_recall_snapshot(
    db: AsyncSession,
    scope: MemoryScope,
    source_rows: list[RecallSnapshot],
) -> bool:
    expected = {row.id for row in source_rows}
    if not expected or len(expected) != len(source_rows):
        return False
    predicates = [
        and_(
            Memory.id == row.id,
            Memory.content_version == row.version,
            Memory.content == row.content,
            Memory.context.is_not_distinct_from(row.context),
            Memory.tags.is_not_distinct_from(row.tags),
            Memory.importance == row.importance,
            Memory.updated_at.is_not_distinct_from(row.updated_at),
        )
        for row in source_rows
    ]
    deleted = set(
        (await db.scalars(delete(Memory).where(scope_filter(scope), or_(*predicates)).returning(Memory.id))).all(),
    )
    if deleted != expected:
        await db.rollback()
        return False
    return True


async def apply_reflection_slot(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    context: str,
    content: str,
    tags: str,
    expected: tuple[int, int] | None,
    source: MemorySource,
) -> bool:
    refs = await _source_refs(db, scope, source)
    if expected is None:
        result = await db.scalar(
            insert(Memory)
            .values(
                user_id=scope.user_id,
                system_preset_id=scope.system_preset_id,
                context=context,
                content=content,
                tags=tags,
                source_kind=source.kind,
                source_refs=refs,
            )
            .on_conflict_do_nothing()
            .returning(Memory.id),
        )
    else:
        memory_id, version = expected
        result = await db.scalar(
            update(Memory)
            .where(
                scope_filter(scope),
                Memory.id == memory_id,
                Memory.context == context,
                Memory.content_version == version,
            )
            .values(
                content=content,
                tags=tags,
                embedding=None,
                content_version=Memory.content_version + 1,
                source_kind=source.kind,
                source_refs=refs,
                updated_at=func.now(),
            )
            .returning(Memory.id),
        )
    return result is not None


async def backfill_memory_embeddings(scope: MemoryScope, rows: list[EmbeddingItem]) -> None:
    validate_memory_scope(scope)
    if not rows:
        return
    try:
        async with session_scope() as db:
            provider = await resolve_embedding_provider(db, scope.user_id)
        vectors = await generate_embeddings([row.content for row in rows], provider, user_id=scope.user_id)
        if vectors is None or len(vectors) != len(rows):
            return
        async with session_scope() as db:
            for row, vector in zip(rows, vectors, strict=True):
                if not isinstance(vector, list) or len(vector) != MEMORY_EMBEDDING_DIM:
                    continue
                await db.execute(
                    update(Memory)
                    .where(
                        scope_filter(scope),
                        Memory.id == row.id,
                        Memory.content_version == row.version,
                        Memory.content == row.content,
                    )
                    .values(embedding=vector, updated_at=Memory.updated_at),
                )
            await db.commit()
    except Exception:
        logger.warning("memory embedding backfill failed", extra={"scope": str(scope)}, exc_info=True)
