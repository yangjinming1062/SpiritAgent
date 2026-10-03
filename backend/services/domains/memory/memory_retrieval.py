import re
from datetime import date, datetime
from itertools import zip_longest
from typing import Literal

from components import session_scope
from modules.memory import MEMORY_EMBEDDING_DIM, Memory
from pydantic import BaseModel
from sqlalchemy import ColumnElement, case, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from services.contracts import MemoryScope
from services.infrastructure.llm import generate_embedding, resolve_embedding_provider

from .memory_namespaces import RESERVED_FROM_RECALL, context_not_in
from .memory_narratives import narrative_date
from .memory_store import active_memory_filter, scope_filter

# RRF 平滑常数（TREC/IR 标准取值）
RRF_K: int = 60

# 单查询可下推到 LIKE 的最大关键词数；各汉字分句轮流取词，长句不独占配额。
SPARSE_QUERY_TERM_MAX: int = 16

_CJK_RUN_PATTERN = re.compile(r"[一-鿿㐀-䶿]+")
# 拉丁等非汉字词：以空白、中英文标点和汉字为界。
_WORD_PATTERN = re.compile(r"[^\s一-鿿㐀-䶿,，。！？!?；;：、\-—_()\[\]【】（）…“”\"《》〈〉「」『』～~/·]+")
_WORD_CHAR_PATTERN = re.compile(r"[^\W_]")


class MemoryRecallResult(BaseModel):
    id: int
    content: str
    context: str | None
    tags: str | None
    importance: float
    basis: str
    kind: Literal["diary", "reflection", "recall"]
    local_date: str | None
    source_kind: str
    expires_at: datetime | None
    score: float
    updated_at: datetime


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
    query_embedding: list[float],
    *,
    limit: int,
) -> list[Memory]:
    """稠密语义检索：用 pgvector ``<=>`` 余弦距离算子在 DB 端排序与截断。"""
    stmt = select(Memory).where(
        scope_filter(scope),
        active_memory_filter(),
        Memory.embedding.isnot(None),
        *[context_not_in(p) for p in RESERVED_FROM_RECALL],
    )
    candidates = stmt.cte("scoped_memories").prefix_with("MATERIALIZED")
    scoped = aliased(Memory, candidates)
    return list(
        (
            await db.scalars(
                select(scoped)
                .order_by(
                    scoped.embedding.cosine_distance(query_embedding),
                    scoped.id,
                )
                .limit(limit),
            )
        ).all(),
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
            *[context_not_in(p) for p in RESERVED_FROM_RECALL],
        )
        .order_by(keyword_match_score(keywords).desc(), Memory.updated_at.desc(), Memory.id.desc())
        .limit(limit)
    )
    return list((await db.scalars(stmt)).all())


async def embed_memory_text(user_id: int, text: str) -> list[float] | None:
    """记忆链路的向量生成入口：独立短会话解析用户级 embedding 供应商，维度校验列宽；未配置、调用失败或维度不符时返回 None，检索降级为纯关键词路径。"""
    if not (text := (text or "").strip()):
        return None
    async with session_scope() as db:
        provider = await resolve_embedding_provider(db, user_id)
    vec = await generate_embedding(text, provider, user_id=user_id, purpose="query")
    return vec if vec and len(vec) == MEMORY_EMBEDDING_DIM else None


async def retrieve_hybrid_memories(
    db: AsyncSession,
    scope: MemoryScope,
    query: str,
    *,
    query_embedding: list[float] | None = None,
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
                Memory.context == f"diary:{diary_date.isoformat()}",
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
    context = mem.context or ""
    kind: Literal["diary", "reflection", "recall"] = (
        "diary" if context.startswith("diary:") else "reflection" if context.startswith("reflection:") else "recall"
    )
    return MemoryRecallResult(
        id=mem.id,
        content=mem.content,
        context=mem.context,
        tags=mem.tags,
        importance=max(0.1, mem.importance or 1.0),
        basis=mem.basis,
        kind=kind,
        local_date=narrative_date(mem.context, mem.source_refs),
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
    query_embedding: list[float] | None = None,
    limit: int = 3,
    min_score: float = 0.002,
) -> list[MemoryRecallResult]:
    """检索与当前语境最相关的若干条记忆，用于主动注入对话。"""
    q_str = (query or "").strip()
    if len(q_str) <= 1:
        return []
    candidates = await retrieve_hybrid_memories(db, scope, q_str, query_embedding=query_embedding, limit=limit)
    return [candidate for candidate in candidates if candidate.score >= min_score]
