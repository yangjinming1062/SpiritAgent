from typing import Any

from components import SETTINGS, session_scope, utc_now
from modules.memory import Memory
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import EmbeddingItem, MemoryScope

from .memory_learning import memory_record
from .memory_namespaces import KIND_TO_PREFIX, RECALL_TAGS, participates_in_recall
from .memory_store import (
    active_memory_filter,
    backfill_memory_embeddings,
    fingerprint_history,
    get_memory,
    memory_write_lock,
    scope_filter,
)

_LIST_DEFAULT_LIMIT = 100
_LIST_MAX_LIMIT = 500


def _row_to_dict(row: Memory) -> dict[str, Any]:
    return {
        **memory_record(row).model_dump(),
        "system_preset_id": row.system_preset_id,
        "importance": row.importance or 1.0,
        "created_at": row.created_at.isoformat(),
    }


async def list_memories(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    kind: str | None = None,
    status: str = "active",
    tag: str | None = None,
    q: str | None = None,
    limit: int | None = _LIST_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """列出用户记忆；内部批处理可用 limit=None 读取全部，分页调用仍限制条数。"""
    if kind is not None and kind not in KIND_TO_PREFIX:
        raise ValueError(f"kind must be one of {sorted(KIND_TO_PREFIX)}")
    if tag is not None and tag not in RECALL_TAGS:
        raise ValueError(f"tag must be in {sorted(RECALL_TAGS)}")
    if limit is not None and (limit <= 0 or limit > _LIST_MAX_LIMIT):
        limit = _LIST_DEFAULT_LIMIT

    if status not in {"active", "candidate", "invalidated", "expired"}:
        raise ValueError("Invalid memory status filter")
    stmt = select(Memory).where(scope_filter(scope))
    if status == "active":
        stmt = stmt.where(active_memory_filter())
    elif status == "expired":
        stmt = stmt.where(Memory.status.in_(("active", "candidate")), Memory.expires_at <= func.now())
    else:
        stmt = stmt.where(Memory.status == status)
        if status == "candidate":
            stmt = stmt.where(or_(Memory.expires_at.is_(None), Memory.expires_at > func.now()))
    if kind is not None:
        stmt = stmt.where(Memory.context.like(KIND_TO_PREFIX[kind] + "%"))
    if tag:
        # tags 是 JSON 字符串；每行只有寥寥数个短 token，子串匹配足够 UI 使用
        stmt = stmt.where(Memory.tags.ilike(f'%"{tag}"%'))
    if q:
        stmt = stmt.where(
            or_(Memory.content.icontains(q, autoescape=True), Memory.context.icontains(q, autoescape=True)),
        )

    rows = (await db.execute(stmt.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit))).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def update_memory(scope: MemoryScope, memory_id: int, *, content: str) -> dict[str, Any] | None:
    """人工编辑作为明确事实重新生效。"""
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")
    async with session_scope() as db:
        await memory_write_lock(db, scope)
        row = await get_memory(db, scope, memory_id)
        if row is None:
            return None
        if (row.context or "").startswith("diary:"):
            raise ValueError("Published diary memories are read-only")
        cap = SETTINGS.memory_recall_max_content_chars
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
        row.basis, row.status = "explicit", "active"
        row.expires_at, row.reviewed_at = None, None
        row.reason = "User edited this memory directly"
        row.source_kind, row.source_refs = "manual", {}
        row.updated_at = utc_now()
        await db.commit()
        result = _row_to_dict(row)
        embedding_item = (
            EmbeddingItem(row.id, row.content, row.content_version) if participates_in_recall(row.context) else None
        )
    if embedding_item is not None:
        await backfill_memory_embeddings(scope, [embedding_item])
    return result


async def memory_counts(db: AsyncSession, scope: MemoryScope) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Memory.status, Memory.expires_at, Memory.context).where(
                scope_filter(scope),
                Memory.status != "forgotten",
            ),
        )
    ).all()
    counts = dict.fromkeys(("active", "candidate", "invalidated", "expired", "user_profile"), 0)
    now = utc_now()
    for status, expiry, context in rows:
        if context and context.startswith("user_profile:"):
            if status == "active":
                counts["user_profile"] += 1
            continue
        if not context or not context.startswith("recall:"):
            continue
        key = "expired" if status in {"active", "candidate"} and expiry and expiry <= now else status
        counts[key] += 1
    return counts
