import asyncio
import hashlib
from datetime import date
from typing import Any

from infrastructure.ai.embedding_space import MEMORY_EMBEDDING_DIM
from infrastructure.ai.llm_client import embedding_model_key, generate_embeddings, resolve_embedding_provider
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from runtime.background import TaskBag
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource, validate_memory_scope
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import ColumnElement, and_, case, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.conversation.evidence import source_conversation_exists
from features.memory.models import MEMORY_SLOT_CONTEXT_PREFIXES, Memory

from .diary_hooks import delete_diary_record

logger = get_logger(__name__)
_EMBEDDING_TASKS: dict[int, asyncio.Task[None]] = {}
_EMBEDDING_BG = TaskBag("memory.embeddings")


def schedule_embedding_rebuild(user_id: int) -> None:
    """查询模型变更后的索引后台补齐；同一账户重复查询合并为已有任务。"""
    if user_id in _EMBEDDING_TASKS or is_user_in_maintenance(user_id):
        return
    task = asyncio.create_task(_rebuild_embeddings(user_id), name=f"memory-embedding-{user_id}")
    _EMBEDDING_TASKS[user_id] = task
    task.add_done_callback(lambda _: _EMBEDDING_TASKS.pop(user_id, None))
    _EMBEDDING_BG.add(task, user_id=user_id)


async def drain_memory_embeddings() -> None:
    await _EMBEDDING_BG.drain()


async def _rebuild_embeddings(user_id: int) -> None:
    try:
        while not is_user_in_maintenance(user_id):
            async with session_scope() as db:
                if not await user_is_active(db, user_id):
                    return
                provider = await resolve_embedding_provider(db, user_id)
                if provider is None:
                    return
                key = embedding_model_key(provider.config)
                rows = list(
                    (
                        await db.scalars(
                            select(Memory)
                            .where(
                                Memory.user_id == user_id,
                                active_memory_filter(),
                                or_(
                                    Memory.embedding.is_(None),
                                    Memory.embedding_model_key.is_(None),
                                    Memory.embedding_model_key != key,
                                ),
                            )
                            .order_by(Memory.id)
                            .limit(100),
                        )
                    ).all(),
                )
            if not rows:
                return
            scopes: dict[str, list[EmbeddingItem]] = {}
            for row in rows:
                scopes.setdefault(row.system_preset_id, []).append(
                    EmbeddingItem(id=row.id, content=row.content, version=row.content_version),
                )
            for preset, items in scopes.items():
                if not await backfill_memory_embeddings(MemoryScope(user_id, preset), items):
                    return
    except Exception:
        logger.warning("memory embedding rebuild failed", extra={"user_id": user_id}, exc_info=True)


def scope_filter(scope: MemoryScope) -> ColumnElement[bool]:
    validate_memory_scope(scope)
    return and_(Memory.user_id == scope.user_id, Memory.system_preset_id == scope.system_preset_id)


async def _source_refs(db: AsyncSession, scope: MemoryScope, source: MemorySource) -> dict[str, Any]:
    validate_memory_scope(scope)
    refs: dict[str, Any] = {}
    if source.session_id is not None:
        conv_id = (
            source.session_id
            if await source_conversation_exists(db, scope.user_id, scope.system_preset_id, source.session_id)
            else None
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
            basis="explicit" if source.kind in {"onboarding", "manual"} else "system",
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
                "basis": "explicit" if source.kind in {"onboarding", "manual"} else "system",
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


async def store_slotted_memory(
    db: AsyncSession,
    scope: MemoryScope,
    context: str,
    content: str,
    tags: str,
    *,
    source: MemorySource,
) -> EmbeddingItem:
    row = await upsert_slotted_memory(db, scope, context, content, tags, source=source)
    return EmbeddingItem(row.id, row.content, row.content_version)


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
    if scope.system_preset_id == "companion" and (row.context or "").startswith("diary:"):
        entry_date = date.fromisoformat(row.context.removeprefix("diary:"))
        diary_id = await delete_diary_record(db, scope.user_id, entry_date)
        if diary_id is not None:
            emit_ws_event(
                db,
                user_id=scope.user_id,
                event_type="companion.diary.deleted",
                payload={"diary_id": diary_id, "entry_date": entry_date.isoformat()},
            )
    forget_record(row)
    emit_ws_event(
        db,
        user_id=scope.user_id,
        event_type="memory.changed",
        payload={"system_preset_id": scope.system_preset_id, "memory_id": row.id},
    )
    await db.commit()
    return True


async def backfill_memory_embeddings(scope: MemoryScope, rows: list[EmbeddingItem]) -> bool:
    validate_memory_scope(scope)
    if not rows:
        return True
    try:
        async with session_scope() as db:
            provider = await resolve_embedding_provider(db, scope.user_id)
        vectors = await generate_embeddings([row.content for row in rows], provider, user_id=scope.user_id)
        if vectors is None or len(vectors) != len(rows):
            return False
        if any(not isinstance(vector, list) or len(vector) != MEMORY_EMBEDDING_DIM for vector in vectors):
            return False
        async with session_scope() as db:
            for row, vector in zip(rows, vectors, strict=True):
                await db.execute(
                    update(Memory)
                    .where(
                        scope_filter(scope),
                        Memory.id == row.id,
                        active_memory_filter(),
                        Memory.content_version == row.version,
                        Memory.content == row.content,
                    )
                    .values(
                        embedding=vector,
                        embedding_model_key=embedding_model_key(provider.config),
                        updated_at=Memory.updated_at,
                    ),
                )
            await db.commit()
        return True
    except Exception:
        logger.warning("memory embedding backfill failed", extra={"scope": str(scope)}, exc_info=True)
        return False
