"""领域检索共用的分词、向量空间与排名，不持有业务数据。"""

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import zip_longest
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .ai.llm_client import embedding_model_key, generate_embedding, resolve_embedding_provider
from .database import session_scope

RRF_K = 60
SEARCH_TERM_MAX = 16
_CJK_RUN = re.compile(r"[一-鿿㐀-䶿]+")
_WORD = re.compile(r"[^\s一-鿿㐀-䶿,，。！？!?；;：、\-—_()\[\]【】（）…“”\"《》〈〉「」『』～~/·]+")
_WORD_CHAR = re.compile(r"[^\W_]")


@dataclass(frozen=True)
class QueryEmbedding:
    vector: list[float]
    model_key: str


class SearchStatus(BaseModel):
    mode: Literal["hybrid", "keyword", "timeline"]
    indexing: bool = False


def content_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def extract_search_terms(query: str, *, limit: int = SEARCH_TERM_MAX) -> list[str]:
    """汉字分句轮流取 2/3-gram，避免长句独占关键词配额。"""
    text = (query or "").lower()
    terms: dict[str, None] = {}
    for word in _WORD.findall(text):
        word = word.strip("'‘’")
        if _WORD_CHAR.search(word) and not (len(word) == 1 and word.isdigit()):
            terms[word] = None
    queues = [
        [run] if len(run) == 1 else [run[i : i + n] for n in (2, 3) for i in range(len(run) - n + 1)]
        for run in _CJK_RUN.findall(text)
    ]
    for items in zip_longest(*queues):
        terms.update(dict.fromkeys(item for item in items if item))
        if len(terms) >= limit:
            break
    return list(terms)[:limit]


def valid_vectors(vectors: list[list[float]], count: int, dimensions: int | None = None) -> bool:
    if len(vectors) != count or not vectors or not vectors[0]:
        return False
    size = dimensions or len(vectors[0])
    return all(len(vector) == size and all(math.isfinite(value) for value in vector) for vector in vectors)


def fuse_rankings(*rankings: list[int]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking, 1):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (RRF_K + rank)
    return scores


async def embed_search_query(user_id: int, query: str) -> QueryEmbedding | None:
    if not query.strip():
        return None
    async with session_scope() as db:
        provider = await resolve_embedding_provider(db, user_id)
    vector = await generate_embedding(query, provider, user_id=user_id, purpose="query")
    if provider is None or vector is None or not valid_vectors([vector], 1):
        return None
    return QueryEmbedding(vector, embedding_model_key(provider.config))


def local_date_bounds(
    date_from: date | None, date_to: date | None, timezone: str
) -> tuple[datetime | None, datetime | None]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期")
    zone = ZoneInfo(timezone)
    start = datetime.combine(date_from, time.min, zone) if date_from is not None else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, zone) if date_to is not None else None
    return start, end
