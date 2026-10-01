import hashlib
from typing import Any

from components import get_logger, session_scope, utc_now
from modules.conversation import Conversation
from modules.memory import MEMORY_EMBEDDING_DIM, MEMORY_SLOT_CONTEXT_PREFIXES, Memory
from sqlalchemy import ColumnElement, and_, case, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import EmbeddingItem, MemoryScope, MemorySource
from services.domains.conversation import validate_memory_scope
from services.infrastructure.llm import generate_embeddings, resolve_embedding_provider

logger = get_logger(__name__)


def scope_filter(scope: MemoryScope) -> ColumnElement[bool]:
    validate_memory_scope(scope)
    return and_(Memory.user_id == scope.user_id, Memory.system_preset_id == scope.system_preset_id)


async def _source_refs(db: AsyncSession, scope: MemoryScope, source: MemorySource) -> dict[str, Any]:
    validate_memory_scope(scope)
    refs: dict[str, Any] = {}
    if source.session_id is not None:
        conv_id = await db.scalar(
            select(Conversation.id).where(
                Conversation.id == source.session_id,
                Conversation.user_id == scope.user_id,
                Conversation.system_preset_id == scope.system_preset_id,
                Conversation.is_automation.is_(False),
            ),
        )
        if conv_id is None:
            raise ValueError("Memory source conversation not found")
        refs["session_id"] = conv_id
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
    await memory_write_lock(db, scope)
    row = Memory(
        user_id=scope.user_id,
        system_preset_id=scope.system_preset_id,
        content=content,
        context=context,
        tags=tags,
        importance=importance,
        source_kind=source.kind,
        source_refs=refs,
        basis="explicit" if source.kind == "manual" else "system",
    )
    db.add(row)
    await db.flush()
    return row


async def get_memory(db: AsyncSession, scope: MemoryScope, memory_id: int) -> Memory | None:
    return await db.scalar(
        select(Memory).where(scope_filter(scope), Memory.id == memory_id).execution_options(populate_existing=True),
    )


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
    await memory_write_lock(db, scope)
    prefix = next((prefix for prefix in MEMORY_SLOT_CONTEXT_PREFIXES if context.startswith(prefix)), None)
    if prefix is None:
        raise ValueError("Context is not a unique memory slot")
    previous = await db.scalar(
        select(Memory).where(scope_filter(scope), Memory.context == context).execution_options(populate_existing=True),
    )
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
            basis="explicit" if source.kind == "onboarding" else "system",
        )
        .on_conflict_do_update(
            index_elements=["user_id", "system_preset_id", "context"],
            index_where=text(f"context LIKE '{prefix}%'"),
            set_={
                "content": content,
                "tags": tags,
                "source_kind": source.kind,
                "source_refs": refs,
                "content_version": Memory.content_version + 1,
                "status": "active",
                "basis": "explicit" if source.kind == "onboarding" else "system",
                "usage": "contextual",
                "reason": "",
                "expires_at": None,
                "reviewed_at": None,
                "evidence": [],
                "history": fingerprint_history(previous) if previous else [],
                "embedding": case((changed, None), else_=Memory.embedding),
                "updated_at": func.now(),
            },
        )
        .returning(Memory)
    )
    return (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one()


def fingerprint_history(row: Memory) -> list[dict[str, Any]]:
    fingerprints = {e["fingerprint"] for e in row.evidence if "fingerprint" in e}
    for old in row.history:
        fingerprints.update(e["fingerprint"] for e in old.get("evidence", []) if "fingerprint" in e)
    return [{"evidence": [{"fingerprint": value} for value in sorted(fingerprints)]}] if fingerprints else []


def active_memory_filter() -> ColumnElement[bool]:
    return and_(Memory.status == "active", or_(Memory.expires_at.is_(None), Memory.expires_at > func.now()))


async def memory_write_lock(db: AsyncSession, scope: MemoryScope) -> None:
    key = int.from_bytes(
        hashlib.sha256(f"memory:{scope.user_id}:{scope.system_preset_id}".encode()).digest()[:8],
        "big",
        signed=True,
    )
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def forget_record(row: Memory) -> None:
    history = fingerprint_history(row)
    row.evidence = history[0]["evidence"] if history else []
    row.source_refs = {}
    row.history = []
    row.content = ""
    row.context = "recall:forgotten"
    row.tags = "[]"
    row.reason = "Forgotten; original evidence must not be reused"
    row.status, row.usage = "forgotten", "contextual"
    row.embedding, row.expires_at = None, None
    row.content_version += 1
    row.updated_at = utc_now()


async def delete_memory(db: AsyncSession, scope: MemoryScope, memory_id: int) -> bool:
    await memory_write_lock(db, scope)
    row = await get_memory(db, scope, memory_id)
    if row is None or row.status == "forgotten":
        return False
    forget_record(row)
    await db.commit()
    return True


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
                        active_memory_filter(),
                        Memory.content_version == row.version,
                        Memory.content == row.content,
                    )
                    .values(embedding=vector, updated_at=Memory.updated_at),
                )
            await db.commit()
    except Exception:
        logger.warning("memory embedding backfill failed", extra={"scope": str(scope)}, exc_info=True)
