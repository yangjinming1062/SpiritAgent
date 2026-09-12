from datetime import datetime
from typing import Any

from components import MAX_RECALL_CONTENT_CHARS, session_scope, utc_now
from modules.memory import Memory
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts.memory import EmbeddingItem, MemoryScope

from .memory_learning import memory_record
from .memory_namespaces import KIND_TO_PREFIX, RECALL_TAGS, participates_in_recall
from .memory_store import (
    active_memory_filter,
    backfill_memory_embeddings,
    get_memory,
    scope_filter,
    update_memory_content,
)

# 界限：列表分页上限与编辑时的长度上限
_LIST_DEFAULT_LIMIT = 100
_LIST_MAX_LIMIT = 500


def _row_to_dict(row: Memory) -> dict[str, Any]:
    return {
        **memory_record(row),
        "id": row.id,
        "system_preset_id": row.system_preset_id,
        "content_version": row.content_version,
        "context": row.context,
        "tags": row.tags,
        "content": row.content,
        "importance": float(getattr(row, "importance", 1.0) or 1.0),
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        "updated_at": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else None,
    }


async def list_memories(
    db: AsyncSession,
    scope: MemoryScope,
    *,
    kind: str | None = None,
    status: str = "active",
    tag: str | None = None,
    q: str | None = None,
    limit: int = _LIST_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """列出用户记忆，可按 kind / tag 过滤，q 对 content 与 context 做子串匹配。"""
    if kind is not None and kind not in KIND_TO_PREFIX:
        raise ValueError(f"kind must be one of {sorted(KIND_TO_PREFIX)}")
    if tag is not None and tag not in RECALL_TAGS:
        raise ValueError(f"tag must be in {sorted(RECALL_TAGS)}")
    if limit <= 0 or limit > _LIST_MAX_LIMIT:
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
        like = f"%{q}%"
        stmt = stmt.where(or_(Memory.content.ilike(like), Memory.context.ilike(like)))

    rows = (await db.execute(stmt.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit))).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def update_memory(scope: MemoryScope, memory_id: int, *, content: str) -> dict[str, Any] | None:
    """人工编辑作为明确事实重新生效。"""
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")
    async with session_scope() as db:
        row = await get_memory(db, scope, memory_id)
        if row is None:
            return None
        cap = MAX_RECALL_CONTENT_CHARS
        if len(content) > cap:
            raise ValueError(f"content exceeds {cap} chars for {row.context or 'recall'}")
        row = await update_memory_content(db, scope, memory_id, content)
        if row is None:
            return None
        await db.commit()
        await db.refresh(row)
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
