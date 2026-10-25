import asyncio
import hashlib
import math
from dataclasses import dataclass
from datetime import date

from infrastructure.ai.llm_client import embedding_model_key, generate_embeddings, resolve_embedding_provider
from infrastructure.ai.providers.base import EmbeddingProvider
from infrastructure.database import session_scope
from infrastructure.domain_locks import transaction_owner, transaction_owns
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from runtime.background import TaskBag
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource, validate_memory_scope
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import ColumnElement, and_, delete, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.conversation.evidence import source_conversation_exists

from .diary_hooks import delete_diary_record
from .memory_namespaces import MEMORY_KINDS
from .models import Memory, MemoryEmbeddingRow
from .records import clear_memory_details

logger = get_logger(__name__)
_EMBEDDING_TASKS: dict[int, asyncio.Task[None]] = {}
_EMBEDDING_TARGETS: dict[int, tuple[str, int]] = {}
_EMBEDDING_BG = TaskBag("memory.embeddings")


def schedule_embedding_rebuild(user_id: int, *, model_key: str, dimensions: int) -> None:
    _EMBEDDING_TARGETS[user_id] = (model_key, dimensions)
    if user_id in _EMBEDDING_TASKS or is_user_in_maintenance(user_id):
        return
    task = asyncio.create_task(_rebuild_embeddings(user_id), name=f"memory-embedding-{user_id}")
    _EMBEDDING_TASKS[user_id] = task
    task.add_done_callback(lambda _: _EMBEDDING_TASKS.pop(user_id, None))
    _EMBEDDING_BG.add(task, user_id=user_id)


async def drain_memory_embeddings() -> None:
    await _EMBEDDING_BG.drain()
    _EMBEDDING_TARGETS.clear()


def invalidate_memory_embeddings(user_id: int) -> None:
    _EMBEDDING_TARGETS.pop(user_id, None)


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
                target = _EMBEDDING_TARGETS.get(user_id)
                dimensions = target[1] if target is not None and target[0] == key else None
                matches = [
                    MemoryEmbeddingRow.memory_id == Memory.id,
                    MemoryEmbeddingRow.model_key == key,
                    MemoryEmbeddingRow.content_version == Memory.content_version,
                ]
                if dimensions is not None:
                    matches.append(MemoryEmbeddingRow.dimensions == dimensions)
                current = exists(select(MemoryEmbeddingRow.id).where(*matches))
                rows = list(
                    await db.scalars(
                        select(Memory)
                        .where(Memory.user_id == user_id, active_memory_filter(), ~current)
                        .order_by(Memory.id)
                        .limit(100)
                    )
                )
            if not rows:
                return
            scopes: dict[str, list[EmbeddingItem]] = {}
            for row in rows:
                scopes.setdefault(row.system_preset_id, []).append(
                    EmbeddingItem(row.id, row.content, row.content_version)
                )
            for preset, items in scopes.items():
                if not await backfill_memory_embeddings(
                    MemoryScope(user_id, preset), items, provider=provider, expected_dimensions=dimensions
                ):
                    return
    except Exception:
        logger.warning("memory embedding rebuild failed", extra={"user_id": user_id}, exc_info=True)


def scope_filter(scope: MemoryScope) -> ColumnElement[bool]:
    validate_memory_scope(scope)
    return and_(Memory.user_id == scope.user_id, Memory.system_preset_id == scope.system_preset_id)


async def validate_source(db: AsyncSession, scope: MemoryScope, source: MemorySource) -> None:
    validate_memory_scope(scope)
    if source.session_id is not None and not await source_conversation_exists(
        db, scope.user_id, scope.system_preset_id, source.session_id
    ):
        raise ValueError("Memory source conversation not found")
    if source.batch_id is not None and len(source.batch_id) > 128:
        raise ValueError("Memory source batch is too long")


def source_date(source: MemorySource) -> date | None:
    try:
        return date.fromisoformat(source.batch_id) if source.batch_id else None
    except ValueError:
        return None


async def create_memory(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    content: str,
    context: str,
    tags: list[str],
    source: MemorySource,
    importance: float = 1.0,
) -> Memory:
    await validate_source(db, scope, source)
    await memory_write_lock(db, scope)
    row = Memory(
        user_id=scope.user_id,
        system_preset_id=scope.system_preset_id,
        content=content,
        context=context,
        kind="recall",
        tags=tags,
        importance=importance,
        source_kind=source.kind,
        source_conversation_id=source.session_id,
        source_batch_id=source.batch_id,
        local_date=source_date(source),
        basis="explicit" if source.kind == "manual" else "system",
    )
    db.add(row)
    await db.flush()
    return row


async def get_memory(db: AsyncSession, scope: MemoryScope, memory_id: int) -> Memory | None:
    return await db.scalar(
        select(Memory).where(scope_filter(scope), Memory.id == memory_id).execution_options(populate_existing=True)
    )


@dataclass(frozen=True)
class MemorySlotWrite:
    kind: str
    slot_key: str
    content: str
    tags: list[str]
    source: MemorySource
    context: str = ""


async def upsert_memory_slots(db: AsyncSession, scope: MemoryScope, slots: list[MemorySlotWrite]) -> list[Memory]:
    if not slots:
        return []
    if len({(slot.kind, slot.slot_key) for slot in slots}) != len(slots):
        raise ValueError("Duplicate memory slots")
    for source in {slot.source for slot in slots}:
        await validate_source(db, scope, source)
    if any(slot.kind not in MEMORY_KINDS or not slot.slot_key for slot in slots):
        raise ValueError("A registered memory kind and slot key are required")
    await memory_write_lock(db, scope)
    values = [
        {
            "user_id": scope.user_id,
            "system_preset_id": scope.system_preset_id,
            "kind": slot.kind,
            "slot_key": slot.slot_key,
            "content": slot.content,
            "context": slot.context,
            "tags": slot.tags,
            "source_kind": slot.source.kind,
            "source_conversation_id": slot.source.session_id,
            "source_batch_id": slot.source.batch_id,
            "local_date": source_date(slot.source),
            "status": "active",
            "basis": "explicit" if slot.source.kind in {"onboarding", "manual"} else "system",
            "usage": "contextual",
            "reason": "",
            "expires_at": None,
            "reviewed_at": None,
        }
        for slot in slots
    ]
    stmt = insert(Memory).values(values)
    updates = {
        key: getattr(stmt.excluded, key)
        for key in values[0]
        if key not in {"user_id", "system_preset_id", "kind", "slot_key"}
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_memories_scope_slot",
        set_={**updates, "content_version": Memory.content_version + 1, "updated_at": func.now()},
    ).returning(Memory)
    rows = list(await db.scalars(stmt.execution_options(populate_existing=True)))
    await clear_memory_details(db, [row.id for row in rows if row.content_version > 1], suppress=False)
    return rows


async def upsert_slotted_memory(
    db: AsyncSession,
    scope: MemoryScope,
    slot_key: str,
    content: str,
    tags: list[str],
    *,
    source: MemorySource,
    kind: str,
    context: str = "",
) -> Memory:
    return (await upsert_memory_slots(db, scope, [MemorySlotWrite(kind, slot_key, content, tags, source, context)]))[0]


async def store_slotted_memory(
    db: AsyncSession,
    scope: MemoryScope,
    slot_key: str,
    content: str,
    tags: list[str],
    *,
    source: MemorySource,
    kind: str,
    context: str = "",
) -> EmbeddingItem:
    row = await upsert_slotted_memory(db, scope, slot_key, content, tags, source=source, kind=kind, context=context)
    return EmbeddingItem(row.id, row.content, row.content_version)


def active_memory_filter() -> ColumnElement[bool]:
    return and_(Memory.status == "active", (Memory.expires_at.is_(None) | (Memory.expires_at > func.now())))


async def memory_write_lock(db: AsyncSession, scope: MemoryScope) -> None:
    key = int.from_bytes(
        hashlib.sha256(f"memory:{scope.user_id}:{scope.system_preset_id}".encode()).digest()[:8], "big", signed=True
    )
    # 同一事务内重复调用公共写入口不增加锁 SQL。
    locks = db.info.setdefault("memory_scope_locks", set())
    transaction = transaction_owner(db)
    if any(held_key == key and transaction_owns(transaction, owner) for owner, held_key in locks):
        return
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    locks.add((transaction_owner(db), key))


def forget_record(row: Memory) -> None:
    row.content, row.context, row.tags = "", "", []
    row.source_conversation_id, row.source_event_id, row.source_batch_id = None, None, None
    row.reason = "Forgotten; original evidence must not be reused"
    row.status, row.usage, row.expires_at = "forgotten", "contextual", None
    row.content_version += 1
    row.updated_at = utc_now()


async def delete_memory(db: AsyncSession, scope: MemoryScope, memory_id: int) -> bool:
    await memory_write_lock(db, scope)
    row = await get_memory(db, scope, memory_id)
    if row is None or row.status == "forgotten":
        return False
    if scope.system_preset_id == "companion" and row.kind == "diary" and row.local_date:
        diary_id = await delete_diary_record(db, scope.user_id, row.local_date)
        if diary_id is not None:
            emit_ws_event(
                db,
                user_id=scope.user_id,
                event_type="companion.diary.deleted",
                payload={"diary_id": diary_id, "entry_date": row.local_date.isoformat()},
            )
    await clear_memory_details(db, [row.id], suppress=True)
    forget_record(row)
    emit_ws_event(
        db,
        user_id=scope.user_id,
        event_type="memory.changed",
        payload={"system_preset_id": scope.system_preset_id, "memory_id": row.id},
    )
    await db.commit()
    return True


async def backfill_memory_embeddings(
    scope: MemoryScope,
    rows: list[EmbeddingItem],
    *,
    provider: EmbeddingProvider | None = None,
    expected_dimensions: int | None = None,
) -> bool:
    validate_memory_scope(scope)
    if not rows:
        return True
    try:
        if provider is None:
            async with session_scope() as db:
                provider = await resolve_embedding_provider(db, scope.user_id)
        if provider is None:
            return False
        vectors = await generate_embeddings([row.content for row in rows], provider, user_id=scope.user_id)
        if vectors is None or len(vectors) != len(rows) or not vectors or not vectors[0]:
            return False
        dimensions = len(vectors[0])
        if expected_dimensions is not None and dimensions != expected_dimensions:
            return False
        if any(len(vector) != dimensions or not all(math.isfinite(value) for value in vector) for vector in vectors):
            return False
        model_key = embedding_model_key(provider.config)
        supplied = {row.id: (row, vector) for row, vector in zip(rows, vectors, strict=True)}
        async with session_scope() as db:
            # 与内容写入共用作用域锁；供应商等待发生在锁与事务之外。
            await memory_write_lock(db, scope)
            current = await db.execute(
                select(Memory.id, Memory.content, Memory.content_version).where(
                    scope_filter(scope), active_memory_filter(), Memory.id.in_(supplied)
                )
            )
            values = [
                {
                    "user_id": scope.user_id,
                    "memory_id": key,
                    "model_key": model_key,
                    "dimensions": dimensions,
                    "content_version": version,
                    "embedding": supplied[key][1],
                }
                for key, content, version in current
                if supplied[key][0].content == content and supplied[key][0].version == version
            ]
            if values:
                stmt = insert(MemoryEmbeddingRow).values(values)
                await db.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_memory_embedding_model",
                        set_={
                            "embedding": stmt.excluded.embedding,
                            "dimensions": stmt.excluded.dimensions,
                            "content_version": stmt.excluded.content_version,
                        },
                    )
                )
                await db.execute(
                    delete(MemoryEmbeddingRow).where(
                        MemoryEmbeddingRow.memory_id.in_([v["memory_id"] for v in values]),
                        MemoryEmbeddingRow.model_key != model_key,
                    )
                )
            await db.commit()
        return True
    except Exception:
        logger.warning("memory embedding backfill failed", extra={"scope": str(scope)}, exc_info=True)
        return False
