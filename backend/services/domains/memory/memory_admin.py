from datetime import datetime
from typing import Any

from components import MAX_AUTO_INJECT_CONTENT_CHARS, MAX_RECALL_CONTENT_CHARS, session_scope
from modules.memory import Memory
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .memory_namespaces import AUTO_INJECT_SLOTS, KIND_TO_PREFIX, RECALL_TAGS, participates_in_recall
from .memory_retrieval import backfill_memory_embeddings

# 界限：列表分页上限与编辑时的长度上限
_LIST_DEFAULT_LIMIT = 100
_LIST_MAX_LIMIT = 500

_OTHER_BUCKET = "other"


async def _owned(db: AsyncSession, user_id: int, memory_id: int) -> Memory | None:
    return (
        await db.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    ).scalar_one_or_none()


async def upsert_slotted_memory(db: AsyncSession, user_id: int, context: str, content: str, tags: str) -> Memory:
    """按 context 插入或更新槽位记忆并返回行（commit 由调用方负责）；内容长度限制与标签格式由调用方负责。"""
    existing = (
        await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context == context))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content != content:
            existing.content = content
            existing.embedding = None
        existing.tags = tags
        return existing
    row = Memory(user_id=user_id, content=content, context=context, tags=tags)
    db.add(row)
    return row


def _row_to_dict(row: Memory) -> dict[str, Any]:
    return {
        "id": row.id,
        "context": row.context,
        "tags": row.tags,
        "content": row.content,
        "importance": float(getattr(row, "importance", 1.0) or 1.0),
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        "updated_at": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else None,
    }


async def list_memories(
    db: AsyncSession,
    user_id: int,
    *,
    kind: str | None = None,
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

    stmt = select(Memory).where(Memory.user_id == user_id)
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


async def update_memory(user_id: int, memory_id: int, *, content: str) -> dict[str, Any] | None:
    """只更新 content；长度上限随 context 而定（auto_inject 槽位必须短，唯一索引与后续整合逻辑依赖这一点）。"""
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")
    async with session_scope() as db:
        row = await _owned(db, user_id, memory_id)
        if row is None:
            return None
        cap = MAX_AUTO_INJECT_CONTENT_CHARS if row.context in AUTO_INJECT_SLOTS else MAX_RECALL_CONTENT_CHARS
        if len(content) > cap:
            raise ValueError(f"content exceeds {cap} chars for {row.context or 'recall'}")
        if row.content != content:
            row.content = content
            row.embedding = None
        await db.commit()
        await db.refresh(row)
        result = _row_to_dict(row)
        embedding_item = (row.id, row.content) if participates_in_recall(row.context) else None
    if embedding_item is not None:
        await backfill_memory_embeddings(user_id, [embedding_item])
    return result


async def delete_memory(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    row = await _owned(db, user_id, memory_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def memory_counts(db: AsyncSession, user_id: int) -> dict[str, int]:
    """按命名空间前缀统计记忆条数；行数本就有界，Python 侧聚合比手写 SQL CASE-WHEN 更划算。"""
    counts: dict[str, int] = dict.fromkeys(KIND_TO_PREFIX, 0)
    counts[_OTHER_BUCKET] = 0
    for (ctx,) in (await db.execute(select(Memory.context).where(Memory.user_id == user_id))).all():
        if ctx is None:
            counts[_OTHER_BUCKET] += 1
            continue
        for label, prefix in KIND_TO_PREFIX.items():
            if ctx.startswith(prefix):
                counts[label] += 1
                break
        else:
            counts[_OTHER_BUCKET] += 1
    return counts
