from datetime import datetime

from infrastructure.config import SETTINGS
from infrastructure.database import session_scope
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import EmbeddingItem, MemoryScope
from sqlalchemy import ColumnElement, and_, delete, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from features.memory.contracts import MemoryConflictError, MemoryDetail, MemoryListItem
from features.memory.memory_namespaces import MEMORY_KINDS, RECALL_TAGS, participates_in_recall
from features.memory.memory_store import (
    active_memory_filter,
    backfill_memory_embeddings,
    forget_record,
    get_memory,
    memory_write_lock,
    scope_filter,
)
from features.memory.models import USER_PROFILE_MAX_CONTENT_CHARS, Memory, MemoryEvidenceRow, MemoryRevision
from features.memory.profile_fields import MemoryProfileChange, validate_profile_value
from features.memory.prompts import CONTEXT_LABELS
from features.memory.records import clear_memory_details, load_evidence, memory_list_item, memory_record, save_revisions

_LIST_DEFAULT_LIMIT = 100
_LIST_MAX_LIMIT = 500


def _status_condition(status: str) -> ColumnElement[bool]:
    if status == "active":
        return active_memory_filter()
    if status == "candidate":
        return and_(Memory.status == "candidate", or_(Memory.expires_at.is_(None), Memory.expires_at > func.now()))
    if status == "invalidated":
        return Memory.status == "invalidated"
    if status == "expired":
        return and_(Memory.status.in_(("active", "candidate")), Memory.expires_at <= func.now())
    raise ValueError("Invalid memory status filter")


def _memory_list_query(
    scope: MemoryScope,
    *,
    kind: str | None = None,
    status: str = "active",
    tag: str | None = None,
    q: str | None = None,
) -> Select[tuple[Memory]]:
    if kind is not None and kind not in MEMORY_KINDS:
        raise ValueError(f"kind must be one of {sorted(MEMORY_KINDS)}")
    if tag is not None and tag not in RECALL_TAGS:
        raise ValueError(f"tag must be in {sorted(RECALL_TAGS)}")
    stmt = select(Memory).where(scope_filter(scope), _status_condition(status))
    if kind is not None:
        stmt = stmt.where(Memory.kind == kind)
    if tag:
        stmt = stmt.where(Memory.tags.contains([tag]))
    if q:
        stmt = stmt.where(
            or_(Memory.content.icontains(q, autoescape=True), Memory.context.icontains(q, autoescape=True)),
        )

    return stmt


async def list_memories(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    kind: str | None = None,
    status: str = "active",
    tag: str | None = None,
    q: str | None = None,
    limit: int | None = _LIST_DEFAULT_LIMIT,
    before: tuple[datetime, int] | None = None,
) -> list[MemoryListItem]:
    """列出用户记忆；内部批处理可用 limit=None 读取全部，分页调用仍限制条数。"""
    if limit is not None and (limit <= 0 or limit > _LIST_MAX_LIMIT + 1):
        limit = _LIST_DEFAULT_LIMIT
    stmt = _memory_list_query(scope, kind=kind, status=status, tag=tag, q=q)
    if before is not None:
        stmt = stmt.where(tuple_(Memory.updated_at, Memory.id) < before)
    rows = (await db.execute(stmt.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit))).scalars().all()
    return [memory_list_item(row) for row in rows]


async def update_user_profile(scope: MemoryScope, changes: list[MemoryProfileChange]) -> list[MemoryListItem]:
    if scope.system_preset_id != "companion":
        raise ValueError("User profile is only available for the companion preset")
    if len({change.field for change in changes}) != len(changes):
        raise ValueError("Each profile field may only be changed once")
    values = {change.field: validate_profile_value(change.field, change.value) for change in changes}
    async with session_scope() as db:
        await memory_write_lock(db, scope)
        rows = list(await db.scalars(select(Memory).where(scope_filter(scope), Memory.kind == "user_profile")))
        slots = {row.slot_key: row for row in rows}
        now = utc_now()
        current = {
            key: row
            for key, row in slots.items()
            if row.status == "active" and (row.expires_at is None or row.expires_at > now)
        }
        for change in changes:
            row = current.get(change.field)
            if (row.content_version if row is not None else 0) != change.expected_version:
                raise MemoryConflictError("这条资料已在其他设备更新，请刷新后核对再保存。")
        changed_rows = []
        forgotten_ids = []
        revised = [slots[change.field] for change in changes if values[change.field] and change.field in slots]
        old_evidence = await load_evidence(db, [row.id for row in revised])
        revisions = [
            {
                "user_id": scope.user_id,
                "memory_id": row.id,
                "content_version": row.content_version,
                "snapshot": memory_record(row, old_evidence[row.id]).model_dump(mode="json"),
                "replaced_at": now,
            }
            for row in revised
            if row.status != "forgotten"
        ]
        for change in changes:
            row = slots.get(change.field)
            value = values[change.field]
            if not value:
                if row is not None:
                    forget_record(row)
                    forgotten_ids.append(row.id)
                continue
            if row is None:
                row = Memory(
                    user_id=scope.user_id,
                    system_preset_id=scope.system_preset_id,
                    kind="user_profile",
                    slot_key=change.field,
                    content_version=1,
                )
                db.add(row)
                rows.append(row)
            else:
                row.content_version += 1
            row.content, row.context, row.tags = value, CONTEXT_LABELS[change.field], ["user_profile"]
            row.source_kind, row.basis, row.status, row.usage = "manual", "explicit", "active", "contextual"
            row.source_conversation_id, row.source_batch_id, row.expires_at, row.reviewed_at = None, None, None, None
            row.reason, row.updated_at = "", now
            changed_rows.append(row)
        await db.flush()
        await clear_memory_details(db, forgotten_ids, suppress=True)
        await save_revisions(db, revisions)
        await clear_memory_details(
            db, [row.id for row in changed_rows if row.content_version > 1], suppress=False, keep_revisions=True
        )
        if changes:
            emit_ws_event(
                db,
                user_id=scope.user_id,
                event_type="memory.changed",
                payload={"system_preset_id": scope.system_preset_id},
            )
        result = [
            memory_list_item(row)
            for row in rows
            if row.status == "active" and (row.expires_at is None or row.expires_at > now)
        ]
        await db.commit()
        return result


async def update_memory(
    scope: MemoryScope,
    memory_id: int,
    *,
    content: str,
    expected_version: int,
) -> MemoryListItem | None:
    """人工编辑重新生效；伙伴自身记录保留表达主体，不转为用户事实。"""
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")
    if "\x00" in content:
        raise ValueError("content must not contain NUL characters")
    async with session_scope() as db:
        await memory_write_lock(db, scope)
        row = await get_memory(db, scope, memory_id)
        if row is None:
            return None
        if row.content_version != expected_version:
            raise MemoryConflictError("这条记忆已在其他设备更新，请刷新后再保存。")
        if row.kind == "diary":
            raise ValueError("Published diary memories are read-only")
        cap = USER_PROFILE_MAX_CONTENT_CHARS if row.kind == "user_profile" else SETTINGS.memory_recall_max_content_chars
        if len(content) > cap:
            raise ValueError(f"content exceeds {cap} chars for {row.context or 'recall'}")
        if row.status == "forgotten":
            return None
        previous_evidence = await load_evidence(db, [row.id])
        await save_revisions(
            db,
            [
                {
                    "user_id": scope.user_id,
                    "memory_id": row.id,
                    "content_version": row.content_version,
                    "snapshot": memory_record(row, previous_evidence[row.id]).model_dump(mode="json"),
                    "replaced_at": utc_now(),
                }
            ],
        )
        await db.execute(delete(MemoryEvidenceRow).where(MemoryEvidenceRow.memory_id == row.id))
        row.content = content
        row.content_version += 1
        is_companion_record = row.basis == "system"
        row.basis, row.status = "system" if is_companion_record else "explicit", "active"
        row.expires_at, row.reviewed_at = None, None
        row.reason = (
            "User corrected this companion record directly"
            if is_companion_record
            else "User edited this memory directly"
        )
        row.source_kind, row.source_conversation_id, row.source_batch_id = "manual", None, None
        row.updated_at = utc_now()
        emit_ws_event(
            db,
            user_id=scope.user_id,
            event_type="memory.changed",
            payload={"system_preset_id": scope.system_preset_id, "memory_id": row.id},
        )
        await db.commit()
        result = memory_list_item(row)
        embedding_item = (
            EmbeddingItem(row.id, row.content, row.content_version) if participates_in_recall(row.kind) else None
        )
    if embedding_item is not None:
        await backfill_memory_embeddings(scope, [embedding_item])
    return result


async def memory_counts(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    include_total: bool = False,
    kind: str | None = None,
    status: str = "active",
    tag: str | None = None,
    q: str | None = None,
) -> dict[str, int]:
    recall = Memory.kind == "recall"
    filters = {
        "active": and_(recall, _status_condition("active")),
        "candidate": and_(recall, _status_condition("candidate")),
        "invalidated": and_(recall, _status_condition("invalidated")),
        "expired": and_(recall, _status_condition("expired")),
        "user_profile": and_(Memory.kind == "user_profile", active_memory_filter()),
    }
    columns = [func.count().filter(condition).label(key) for key, condition in filters.items()]
    if include_total:
        filtered = _memory_list_query(scope, kind=kind, status=status, tag=tag, q=q).subquery()
        columns.append(select(func.count()).select_from(filtered).correlate(None).scalar_subquery().label("total"))
    row = (await db.execute(select(*columns).where(scope_filter(scope)))).one()
    return dict(row._mapping)


async def memory_detail(db: AsyncSession, scope: MemoryScope, memory_id: int) -> MemoryDetail | None:
    row = await get_memory(db, scope, memory_id)
    if row is None:
        return None
    evidence = await load_evidence(db, [row.id])
    revisions = list(
        await db.scalars(
            select(MemoryRevision.snapshot)
            .where(MemoryRevision.user_id == scope.user_id, MemoryRevision.memory_id == row.id)
            .order_by(MemoryRevision.content_version.desc())
        )
    )
    return MemoryDetail(**memory_list_item(row).model_dump(), evidence=evidence[row.id], revisions=revisions)
