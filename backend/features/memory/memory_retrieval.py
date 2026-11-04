from datetime import datetime
from typing import Literal

from infrastructure.api_ids import ResourceId
from infrastructure.search import QueryEmbedding, embed_search_query, extract_search_terms, fuse_rankings
from pydantic import BaseModel
from runtime.memory_scope import MemoryScope
from sqlalchemy import ColumnElement, case, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.memory_namespaces import RESERVED_FROM_RECALL
from features.memory.memory_store import active_memory_filter, schedule_embedding_rebuild, scope_filter
from features.memory.models import Memory, MemoryEmbeddingRow


class MemoryRecallResult(BaseModel):
    id: ResourceId
    content: str
    context: str | None
    tags: list[str]
    importance: float
    basis: str
    kind: Literal["reflection", "recall"]
    local_date: str | None
    source_kind: str
    expires_at: datetime | None
    score: float
    updated_at: datetime


def keyword_match_score(keywords: list[str]) -> ColumnElement[float]:
    """数据库端的关键词命中分：逐词累加，正文命中记 1，仅上下文命中记 0.5。"""
    return sum(
        (
            case(
                (Memory.content.icontains(kw, autoescape=True), 1.0),
                (Memory.context.icontains(kw, autoescape=True), 0.5),
                else_=0.0,
            )
            for kw in keywords
        ),
        literal(0.0),
    )


async def _dense_search(
    db: AsyncSession, scope: MemoryScope, query_embedding: QueryEmbedding, *, limit: int
) -> list[Memory]:
    """稠密语义检索：用 pgvector ``<=>`` 余弦距离算子在 DB 端排序与截断。"""
    # 精确计算只作用于相同作用域、模型、维度和内容版本的向量。
    candidates = (
        select(Memory.id.label("memory_id"), MemoryEmbeddingRow.embedding)
        .join(MemoryEmbeddingRow, MemoryEmbeddingRow.memory_id == Memory.id)
        .where(
            scope_filter(scope),
            active_memory_filter(),
            Memory.kind.not_in(RESERVED_FROM_RECALL),
            MemoryEmbeddingRow.model_key == query_embedding.model_key,
            MemoryEmbeddingRow.dimensions == len(query_embedding.vector),
            MemoryEmbeddingRow.content_version == Memory.content_version,
        )
        .cte("scoped_embeddings")
        .prefix_with("MATERIALIZED")
    )
    return list(
        await db.scalars(
            select(Memory)
            .join(candidates, candidates.c.memory_id == Memory.id)
            .order_by(candidates.c.embedding.cosine_distance(query_embedding.vector), Memory.id)
            .limit(limit)
        )
    )


async def _sparse_search(db: AsyncSession, scope: MemoryScope, keywords: list[str], *, limit: int) -> list[Memory]:
    """稀疏关键词检索：跨 content/context 的 ILIKE OR 拉取候选（受益于 ``ix_memories_content_trgm`` / ``ix_memories_context_trgm`` GIN trigram 索引），在数据库端按关键词命中分与 updated_at 排序后截断。"""
    if not keywords:
        return []
    conditions = [
        c
        for kw in keywords
        for c in (Memory.content.icontains(kw, autoescape=True), Memory.context.icontains(kw, autoescape=True))
    ]
    stmt = (
        select(Memory)
        .where(scope_filter(scope), active_memory_filter(), or_(*conditions), Memory.kind.not_in(RESERVED_FROM_RECALL))
        .order_by(keyword_match_score(keywords).desc(), Memory.updated_at.desc(), Memory.id.desc())
        .limit(limit)
    )
    return list((await db.scalars(stmt)).all())


async def embed_memory_text(user_id: int, text: str) -> QueryEmbedding | None:
    """记忆链路的向量生成入口：独立短会话解析用户级 embedding 供应商，校验非空有限向量；未配置或调用失败时返回 None，检索降级为纯关键词路径。"""
    vector = await embed_search_query(user_id, text)
    if vector is not None:
        schedule_embedding_rebuild(user_id, model_key=vector.model_key, dimensions=len(vector.vector))
    return vector


async def retrieve_hybrid_memories(
    db: AsyncSession, scope: MemoryScope, query: str, *, query_embedding: QueryEmbedding | None = None, limit: int = 10
) -> list[MemoryRecallResult]:
    """稠密与稀疏检索用 RRF 融合；相关性相同时再按重要性、更新时间排序。"""
    q_str = (query or "").strip()
    if not q_str and not query_embedding:
        return []

    keywords = extract_search_terms(q_str)

    dense_candidates: list[Memory] = []
    if query_embedding:
        dense_candidates = await _dense_search(db, scope, query_embedding, limit=limit * 2)
    sparse_candidates = await _sparse_search(db, scope, keywords, limit=limit * 2)

    if not dense_candidates and not sparse_candidates:
        return []

    all_memories: dict[int, Memory] = {r.id: r for r in dense_candidates + sparse_candidates}

    scores = fuse_rankings([row.id for row in dense_candidates], [row.id for row in sparse_candidates])
    results = [_memory_result(memory, scores[identifier]) for identifier, memory in all_memories.items()]

    results.sort(key=lambda result: (result.score, result.importance, result.updated_at, result.id), reverse=True)
    return results[:limit]


def _memory_result(mem: Memory, score: float) -> MemoryRecallResult:
    kind: Literal["reflection", "recall"] = "reflection" if mem.kind == "reflection" else "recall"
    return MemoryRecallResult(
        id=mem.id,
        content=mem.content,
        context=mem.context,
        tags=mem.tags,
        importance=max(0.1, mem.importance or 1.0),
        basis=mem.basis,
        kind=kind,
        local_date=mem.local_date.isoformat() if mem.local_date else None,
        source_kind=mem.source_kind,
        expires_at=mem.expires_at,
        score=score,
        updated_at=mem.updated_at,
    )


async def retrieve_proactive_memories(
    db: AsyncSession, scope: MemoryScope, query: str, *, query_embedding: QueryEmbedding | None = None, limit: int = 3
) -> list[MemoryRecallResult]:
    """检索与当前语境最相关的若干条记忆，用于主动注入对话。"""
    q_str = (query or "").strip()
    if len(q_str) <= 1:
        return []
    return await retrieve_hybrid_memories(db, scope, q_str, query_embedding=query_embedding, limit=limit)
