import math
import re
from typing import Any

from components import get_logger, session_scope, utc_now
from modules.memory import Memory
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from services.contracts.memory import MemoryScope
from services.infrastructure.llm import (
    EmbeddingProvider,
    generate_embedding,
    resolve_embedding_provider,
)

from .memory_namespaces import RESERVED_FROM_RECALL, context_not_in
from .memory_store import scope_filter

logger = get_logger(__name__)

# RRF 平滑常数（TREC/IR 标准取值）
RRF_K: int = 60
# 艾宾浩斯遗忘衰减系数（半衰期约 14 天）
TIME_DECAY_LAMBDA: float = 0.05
# 衰减保底值，避免长期记忆被归零
TIME_DECAY_FLOOR: float = 0.30

# 单查询可下推到 LIKE 的最大关键词数；选 16 而非示例值 8，保留 2/3-gram 共存窗口，4-gram 由 PG pg_trgm 索引接手。
SPARSE_QUERY_TERM_MAX: int = 16

# memories.embedding 列宽（pgvector Vector(1536)）；维度不符的供应商向量直接丢弃而非落库报错。
MEMORY_EMBEDDING_DIM = 1536

_CJK_PATTERN = re.compile(r"[一-鿿㐀-䶿]")
_CJK_RUN_PATTERN = re.compile(r"[一-鿿㐀-䶿]+")
_NON_CJK_RUN_PATTERN = re.compile(r"[^一-鿿㐀-䶿]+")
_TOKEN_SPLIT_PATTERN = re.compile(r"[\s,，。！？!?；;、\-—_()\[\]【】()（）]+")


def _compute_time_decay(updated_at: Any, now: Any) -> float:
    if not updated_at or not now:
        return 1.0
    delta_days = max(0.0, (now - updated_at).total_seconds() / 86400.0)
    return TIME_DECAY_FLOOR + (1.0 - TIME_DECAY_FLOOR) * math.exp(-TIME_DECAY_LAMBDA * delta_days)


def _extract_search_terms(query: str) -> list[str]:
    """结构化词条提取：优先保留拉丁/专有名词 token 与 CJK 完整词段，辅以 2/3-gram 滑动窗口，截断至 SPARSE_QUERY_TERM_MAX。"""
    q = (query or "").strip()
    if not q:
        return []

    tokens: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        term = term.strip().lower()
        if not term or term in seen:
            return
        # 单字符过滤：CJK 至少 2 字符；拉丁单字符保留（专有名 R/Go 等）。
        if _CJK_PATTERN.search(term) and len(term) < 2:
            return
        seen.add(term)
        tokens.append(term)

    # 1. 优先提取拉丁/英文/数字 token（如 postgresql, python, v2 等高信息量词汇）
    for raw in _TOKEN_SPLIT_PATTERN.split(q):
        for latin in _NON_CJK_RUN_PATTERN.findall(raw):
            _add(latin)

    # 2. 遍历所有分句，提取 2-gram 滑动窗口（每个分句均能获得关键词代表，避免首句独占配额）
    for raw in _TOKEN_SPLIT_PATTERN.split(q):
        for run in _CJK_RUN_PATTERN.findall(raw):
            for i in range(len(run) - 1):
                _add(run[i : i + 2])

    # 3. 补充 3-gram 滑动窗口
    for raw in _TOKEN_SPLIT_PATTERN.split(q):
        for run in _CJK_RUN_PATTERN.findall(raw):
            for i in range(len(run) - 2):
                _add(run[i : i + 3])

    return tokens[:SPARSE_QUERY_TERM_MAX]


async def _dense_search(
    db: AsyncSession,
    scope: MemoryScope,
    query_embedding: list[float],
    limit: int = 30,
    excluded_namespaces: frozenset[str] = RESERVED_FROM_RECALL,
) -> list[Memory]:
    """稠密语义检索：用 pgvector ``<=>`` 余弦距离算子在 DB 端排序与截断。"""
    stmt = select(Memory).where(
        scope_filter(scope),
        Memory.embedding.isnot(None),
        *[context_not_in(p) for p in excluded_namespaces],
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
    excluded_namespaces: frozenset[str] = RESERVED_FROM_RECALL,
) -> list[Memory]:
    """稀疏关键词检索：跨 content/context 的 ILIKE OR 拉取候选（受益于 ``ix_memories_content_trgm`` / ``ix_memories_context_trgm`` GIN trigram 索引），按关键词命中率与 updated_at 在 Python 端排序截断。"""
    if not keywords:
        return []
    conditions = [c for kw in keywords for c in (Memory.content.ilike(f"%{kw}%"), Memory.context.ilike(f"%{kw}%"))]
    stmt = (
        select(Memory)
        .where(
            scope_filter(scope),
            or_(*conditions),
            *[context_not_in(p) for p in excluded_namespaces],
        )
        .order_by(Memory.updated_at.desc())
        .limit(limit * 2)
    )
    rows = (await db.execute(stmt)).scalars().all()
    scored = []
    for r in rows:
        c_low = (r.content or "").lower()
        ctx_low = (r.context or "").lower()
        hits = sum(1.0 if kw in c_low else (0.5 if kw in ctx_low else 0.0) for kw in keywords)
        score = hits / max(len(keywords), 1)
        scored.append((score, r))
    scored.sort(key=lambda x: (x[0], x[1].updated_at), reverse=True)
    return [r for _, r in scored[:limit]]


async def embed_memory_text(user_id: int, text: str) -> list[float] | None:
    """记忆链路的向量生成入口：独立短会话解析用户级 embedding 供应商（含 OpenAI 兼容回退），
    维度校验列宽；未配置、调用失败或维度不符时返回 None，检索降级为纯关键词路径。"""
    if not (text := (text or "").strip()):
        return None
    provider = await _resolve_memory_embedding_provider(user_id)
    vec = await generate_embedding(text, provider, user_id=user_id)
    return vec if vec and len(vec) == MEMORY_EMBEDDING_DIM else None


async def _resolve_memory_embedding_provider(user_id: int) -> EmbeddingProvider | None:
    async with session_scope() as db:
        return await resolve_embedding_provider(db, user_id)


async def retrieve_hybrid_memories(
    db: AsyncSession,
    scope: MemoryScope,
    query: str,
    *,
    query_embedding: list[float] | None = None,
    limit: int = 10,
    excluded_namespaces: frozenset[str] = RESERVED_FROM_RECALL,
) -> list[dict[str, Any]]:
    """稠密与稀疏检索的混合搜索，用 RRF 融合排名并叠加艾宾浩斯时间衰减。"""
    q_str = (query or "").strip()
    if not q_str and not query_embedding:
        return []

    keywords = _extract_search_terms(q_str)

    dense_candidates: list[Memory] = []
    if query_embedding:
        dense_candidates = await _dense_search(
            db,
            scope,
            query_embedding,
            limit=limit * 2,
            excluded_namespaces=excluded_namespaces,
        )

    sparse_candidates: list[Memory] = []
    if q_str or keywords:
        sparse_candidates = await _sparse_search(
            db,
            scope,
            keywords,
            limit=limit * 2,
            excluded_namespaces=excluded_namespaces,
        )

    if not dense_candidates and not sparse_candidates:
        return []

    all_memories: dict[int, Memory] = {r.id: r for r in dense_candidates + sparse_candidates}

    dense_ranks = {r.id: rank + 1 for rank, r in enumerate(dense_candidates)}
    sparse_ranks = {r.id: rank + 1 for rank, r in enumerate(sparse_candidates)}

    now = utc_now()
    results = []

    for mem_id, mem in all_memories.items():
        rrf_score = 0.0
        if mem_id in dense_ranks:
            rrf_score += 1.0 / (RRF_K + dense_ranks[mem_id])
        if mem_id in sparse_ranks:
            rrf_score += 1.0 / (RRF_K + sparse_ranks[mem_id])

        decay = _compute_time_decay(mem.updated_at, now)
        importance = max(0.1, float(getattr(mem, "importance", 1.0) or 1.0))
        final_score = rrf_score * decay * importance

        results.append(
            {
                "id": mem.id,
                "content": mem.content,
                "context": mem.context,
                "tags": mem.tags,
                "importance": importance,
                "score": final_score,
                "updated_at": mem.updated_at,
            },
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


async def retrieve_proactive_memories(
    db: AsyncSession,
    scope: MemoryScope,
    query: str,
    *,
    query_embedding: list[float] | None = None,
    limit: int = 3,
    min_score: float = 0.002,
) -> list[dict[str, Any]]:
    """检索与当前语境最相关的若干条记忆，用于主动注入对话。"""
    q_str = (query or "").strip()
    if not q_str or len(q_str) <= 1:
        return []
    candidates = await retrieve_hybrid_memories(db, scope, q_str, query_embedding=query_embedding, limit=limit)
    return [c for c in candidates if c["score"] >= min_score]
