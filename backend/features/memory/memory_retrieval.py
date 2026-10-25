import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from itertools import zip_longest
from typing import Literal

from infrastructure.ai.llm_client import embedding_model_key, generate_embedding, resolve_embedding_provider
from infrastructure.api_ids import ResourceId
from infrastructure.database import session_scope
from pydantic import BaseModel
from runtime.memory_scope import MemoryScope
from sqlalchemy import ColumnElement, case, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.memory_namespaces import RESERVED_FROM_RECALL
from features.memory.memory_store import active_memory_filter, schedule_embedding_rebuild, scope_filter
from features.memory.models import Memory, MemoryEmbeddingRow

# RRF 平滑常数（TREC/IR 标准取值）
RRF_K: int = 60

# 单查询可下推到 LIKE 的最大关键词数；各汉字分句轮流取词，长句不独占配额。
SPARSE_QUERY_TERM_MAX: int = 16

_CJK_RUN_PATTERN = re.compile(r"[一-鿿㐀-䶿]+")
# 拉丁等非汉字词：以空白、中英文标点和汉字为界。
_WORD_PATTERN = re.compile(r"[^\s一-鿿㐀-䶿,，。！？!?；;：、\-—_()\[\]【】（）…“”\"《》〈〉「」『』～~/·]+")
_WORD_CHAR_PATTERN = re.compile(r"[^\W_]")


class MemoryRecallResult(BaseModel):
    id: ResourceId
    content: str
    context: str | None
    tags: list[str]
    importance: float
    basis: str
    kind: Literal["diary", "reflection", "recall"]
    local_date: str | None
    source_kind: str
    expires_at: datetime | None
    score: float
    updated_at: datetime


@dataclass(frozen=True)
class MemoryEmbedding:
    vector: list[float]
    model_key: str


def _cjk_ngrams(run: str) -> list[str]:
    """汉字分句的候选词：先全部 2-gram，再全部 3-gram。"""
    return [run[i : i + n] for n in (2, 3) for i in range(len(run) - n + 1)]


def extract_search_terms(query: str, *, limit: int = SPARSE_QUERY_TERM_MAX) -> list[str]:
    """提取至多 limit 个关键词：拉丁等非汉字词在前，其后各汉字分句轮流取词，避免首个长句独占配额。"""
    q = (query or "").lower()
    terms: dict[str, None] = {}
    for word in _WORD_PATTERN.findall(q):
        word = word.strip("'‘’")  # 只去词边缘的引号，don't 这类词内撇号保留
        # 无字母数字的符号串与单个数字不构成关键词；拉丁单字符保留（专有名 R/Go 等）。
        if _WORD_CHAR_PATTERN.search(word) and not (len(word) == 1 and word.isdigit()):
            terms[word] = None
    queues = [_cjk_ngrams(run) for run in _CJK_RUN_PATTERN.findall(q)]
    for round_terms in zip_longest(*queues):
        terms.update(dict.fromkeys(term for term in round_terms if term))
        if len(terms) >= limit:
            break
    return list(terms)[:limit]


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
    db: AsyncSession,
    scope: MemoryScope,
    query_embedding: MemoryEmbedding,
    *,
    limit: int,
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


async def _sparse_search(
    db: AsyncSession,
    scope: MemoryScope,
    keywords: list[str],
    *,
    limit: int,
) -> list[Memory]:
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
        .where(
            scope_filter(scope),
            active_memory_filter(),
            or_(*conditions),
            Memory.kind.not_in(RESERVED_FROM_RECALL),
        )
        .order_by(keyword_match_score(keywords).desc(), Memory.updated_at.desc(), Memory.id.desc())
        .limit(limit)
    )
    return list((await db.scalars(stmt)).all())


async def embed_memory_text(user_id: int, text: str) -> MemoryEmbedding | None:
    """记忆链路的向量生成入口：独立短会话解析用户级 embedding 供应商，校验非空有限向量；未配置或调用失败时返回 None，检索降级为纯关键词路径。"""
    if not (text := (text or "").strip()):
        return None
    async with session_scope() as db:
        provider = await resolve_embedding_provider(db, user_id)
    vec = await generate_embedding(text, provider, user_id=user_id, purpose="query")
    if provider is None or not vec or not all(math.isfinite(value) for value in vec):
        return None
    schedule_embedding_rebuild(user_id, model_key=embedding_model_key(provider.config), dimensions=len(vec))
    return MemoryEmbedding(vec, embedding_model_key(provider.config))


async def retrieve_hybrid_memories(
    db: AsyncSession,
    scope: MemoryScope,
    query: str,
    *,
    query_embedding: MemoryEmbedding | None = None,
    limit: int = 10,
    diary_date: date | None = None,
) -> list[MemoryRecallResult]:
    """稠密与稀疏检索用 RRF 融合；相关性相同时再按重要性、更新时间排序。"""
    q_str = (query or "").strip()
    if diary_date is not None:
        if scope.system_preset_id != "companion":
            return []
        row = await db.scalar(
            select(Memory).where(
                scope_filter(scope),
                active_memory_filter(),
                Memory.kind == "diary",
                Memory.local_date == diary_date,
            ),
        )
        return [_memory_result(row, 1.0)] if row is not None else []
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

    dense_ranks = {r.id: rank + 1 for rank, r in enumerate(dense_candidates)}
    sparse_ranks = {r.id: rank + 1 for rank, r in enumerate(sparse_candidates)}

    results = []

    for mem_id, mem in all_memories.items():
        rrf_score = 0.0
        if mem_id in dense_ranks:
            rrf_score += 1.0 / (RRF_K + dense_ranks[mem_id])
        if mem_id in sparse_ranks:
            rrf_score += 1.0 / (RRF_K + sparse_ranks[mem_id])

        results.append(_memory_result(mem, rrf_score))

    results.sort(key=lambda result: (result.score, result.importance, result.updated_at, result.id), reverse=True)
    return results[:limit]


def _memory_result(mem: Memory, score: float) -> MemoryRecallResult:
    kind: Literal["diary", "reflection", "recall"] = (
        "diary" if mem.kind == "diary" else "reflection" if mem.kind == "reflection" else "recall"
    )
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
    db: AsyncSession,
    scope: MemoryScope,
    query: str,
    *,
    query_embedding: MemoryEmbedding | None = None,
    limit: int = 3,
) -> list[MemoryRecallResult]:
    """检索与当前语境最相关的若干条记忆，用于主动注入对话。"""
    q_str = (query or "").strip()
    if len(q_str) <= 1:
        return []
    return await retrieve_hybrid_memories(db, scope, q_str, query_embedding=query_embedding, limit=limit)
