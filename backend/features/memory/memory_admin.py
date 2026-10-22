from infrastructure.config import SETTINGS
from infrastructure.database import session_scope
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import EmbeddingItem, MemoryScope, MemorySource
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from features.memory.contracts import MemoryConflictError, MemoryListItem
from features.memory.memory_learning import memory_record
from features.memory.memory_namespaces import KIND_TO_PREFIX, RECALL_TAGS, participates_in_recall
from features.memory.memory_store import (
    active_memory_filter,
    backfill_memory_embeddings,
    fingerprint_history,
    forget_record,
    get_memory,
    memory_write_lock,
    scope_filter,
    upsert_slotted_memory,
)
from features.memory.models import USER_PROFILE_MAX_CONTENT_CHARS, Memory
from features.memory.profile_fields import MemoryProfileChange, validate_profile_value
from features.memory.prompts import CONTEXT_LABELS

_LIST_DEFAULT_LIMIT = 100
_LIST_MAX_LIMIT = 500


def _list_item(row: Memory) -> MemoryListItem:
    return MemoryListItem(
        **memory_record(row).model_dump(),
        system_preset_id=row.system_preset_id,
        importance=row.importance or 1.0,
        created_at=row.created_at.isoformat(),
    )


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
    if kind is not None and kind not in KIND_TO_PREFIX:
        raise ValueError(f"kind must be one of {sorted(KIND_TO_PREFIX)}")
    if tag is not None and tag not in RECALL_TAGS:
        raise ValueError(f"tag must be in {sorted(RECALL_TAGS)}")
    stmt = select(Memory).where(scope_filter(scope), _status_condition(status))
    if kind is not None:
        stmt = stmt.where(Memory.context.startswith(KIND_TO_PREFIX[kind], autoescape=True))
    if tag:
        # tags 是 JSON 字符串；每行只有寥寥数个短 token，子串匹配足够 UI 使用
        stmt = stmt.where(Memory.tags.ilike(f'%"{tag}"%'))
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
    offset: int = 0,
) -> list[MemoryListItem]:
    """列出用户记忆；内部批处理可用 limit=None 读取全部，分页调用仍限制条数。"""
    if limit is not None and (limit <= 0 or limit > _LIST_MAX_LIMIT):
        limit = _LIST_DEFAULT_LIMIT
    if offset < 0:
        raise ValueError("offset must be non-negative")
    stmt = _memory_list_query(scope, kind=kind, status=status, tag=tag, q=q)
    rows = (
        (await db.execute(stmt.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return [_list_item(row) for row in rows]


async def memory_list_total(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    kind: str | None,
    status: str,
    tag: str | None,
    q: str | None,
) -> int:
    stmt = _memory_list_query(scope, kind=kind, status=status, tag=tag, q=q)
    return await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


async def update_user_profile(scope: MemoryScope, changes: list[MemoryProfileChange]) -> list[MemoryListItem]:
    if scope.system_preset_id != "companion":
        raise ValueError("User profile is only available for the companion preset")
    if len({change.field for change in changes}) != len(changes):
        raise ValueError("Each profile field may only be changed once")
    values = {change.field: validate_profile_value(change.field, change.value) for change in changes}
    async with session_scope() as db:
        await memory_write_lock(db, scope)
        rows = list(
            (
                await db.scalars(
                    select(Memory).where(
                        scope_filter(scope),
                        Memory.context.in_(CONTEXT_LABELS.values()),
                    ),
                )
            ).all(),
        )
        slots = {row.context: row for row in rows}
        now = utc_now()
        current = {
            row.context: row
            for row in rows
            if row.status == "active" and (row.expires_at is None or row.expires_at > now)
        }
        for change in changes:
            row = current.get(CONTEXT_LABELS[change.field])
            if (row.content_version if row is not None else 0) != change.expected_version:
                raise MemoryConflictError("这条资料已在其他设备更新，请刷新后核对再保存。")
        for change in changes:
            context = CONTEXT_LABELS[change.field]
            value = values[change.field]
            if not value:
                row = slots.get(context)
                if row is not None:
                    forget_record(row)
            else:
                await upsert_slotted_memory(
                    db,
                    scope,
                    context,
                    value,
                    '["user_profile"]',
                    source=MemorySource("manual"),
                )
        if changes:
            emit_ws_event(
                db,
                user_id=scope.user_id,
                event_type="memory.changed",
                payload={"system_preset_id": scope.system_preset_id},
            )
        await db.flush()
        result = await list_memories(db, scope, kind="user_profile", limit=None)
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
        if (row.context or "").startswith("diary:"):
            raise ValueError("Published diary memories are read-only")
        cap = (
            USER_PROFILE_MAX_CONTENT_CHARS
            if (row.context or "").startswith("user_profile:")
            else SETTINGS.memory_recall_max_content_chars
        )
        if len(content) > cap:
            raise ValueError(f"content exceeds {cap} chars for {row.context or 'recall'}")
        if row.status == "forgotten":
            return None
        row.history = fingerprint_history(row)
        row.evidence = []
        if row.content != content:
            row.embedding = None
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
        row.source_kind, row.source_refs = "manual", {}
        row.updated_at = utc_now()
        emit_ws_event(
            db,
            user_id=scope.user_id,
            event_type="memory.changed",
            payload={"system_preset_id": scope.system_preset_id, "memory_id": row.id},
        )
        await db.commit()
        result = _list_item(row)
        embedding_item = (
            EmbeddingItem(row.id, row.content, row.content_version) if participates_in_recall(row.context) else None
        )
    if embedding_item is not None:
        await backfill_memory_embeddings(scope, [embedding_item])
    return result


async def memory_counts(db: AsyncSession, scope: MemoryScope) -> dict[str, int]:
    recall = Memory.context.like("recall:%")
    filters = {
        "active": and_(recall, _status_condition("active")),
        "candidate": and_(recall, _status_condition("candidate")),
        "invalidated": and_(recall, _status_condition("invalidated")),
        "expired": and_(recall, _status_condition("expired")),
        "user_profile": and_(Memory.context.startswith("user_profile:", autoescape=True), active_memory_filter()),
    }
    row = (
        await db.execute(
            select(*(func.count().filter(condition).label(key) for key, condition in filters.items())).where(
                scope_filter(scope),
            ),
        )
    ).one()
    return dict(row._mapping)
