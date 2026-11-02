"""由业务拥有的可重建检索表；这里只共享确定性的索引操作。"""

from collections.abc import Sequence
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Select,
    String,
    Text,
    UniqueConstraint,
    case,
    exists,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .models import ModelBase
from .search import QueryEmbedding, SearchStatus, content_fingerprint, extract_search_terms, fuse_rankings


class SearchIndexRow(ModelBase):
    __abstract__ = True

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    record_id: Mapped[int] = mapped_column(BigInteger)
    search_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    model_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedded_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


def search_index_constraints(name: str) -> tuple[UniqueConstraint, CheckConstraint, Index]:
    return (
        UniqueConstraint("user_id", "record_id", name=f"uq_{name}_record"),
        CheckConstraint(
            "(embedding IS NULL AND model_key IS NULL AND dimensions IS NULL AND embedded_hash IS NULL) OR "
            "(embedding IS NOT NULL AND model_key IS NOT NULL AND dimensions IS NOT NULL AND dimensions > 0 "
            "AND embedded_hash IS NOT NULL AND vector_dims(embedding) = dimensions AND embedded_hash = content_hash)",
            name="embedding_space",
        ),
        Index(f"ix_{name}_text", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
    )


@dataclass(frozen=True)
class SearchDocument:
    record_id: int
    text: str


async def upsert_search_documents(
    db: AsyncSession, model: type[SearchIndexRow], user_id: int, documents: Sequence[SearchDocument]
) -> None:
    for start in range(0, len(documents), 100):
        values = [
            {
                "user_id": user_id,
                "record_id": item.record_id,
                "search_text": item.text,
                "content_hash": content_fingerprint(item.text),
            }
            for item in documents[start : start + 100]
        ]
        statement = insert(model).values(values)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "record_id"],
                set_={
                    "search_text": statement.excluded.search_text,
                    "content_hash": statement.excluded.content_hash,
                    "embedding": None,
                    "model_key": None,
                    "dimensions": None,
                    "embedded_hash": None,
                },
                where=model.content_hash != statement.excluded.content_hash,
            )
        )


async def search_documents(
    db: AsyncSession,
    model: type[SearchIndexRow],
    user_id: int,
    records: Select[tuple[int]],
    query: str,
    *,
    vector: QueryEmbedding | None,
    limit: int,
) -> tuple[list[int], SearchStatus]:
    scope = [model.user_id == user_id, model.record_id.in_(records)]
    keywords = extract_search_terms(query)
    sparse: list[int] = []
    if keywords:
        score = sum(case((model.search_text.icontains(term, autoescape=True), 1.0), else_=0.0) for term in keywords)
        sparse = list(
            await db.scalars(
                select(model.record_id)
                .where(*scope, or_(*(model.search_text.icontains(term, autoescape=True) for term in keywords)))
                .order_by(score.desc(), model.record_id.desc())
                .limit(limit * 2)
            )
        )
    dense: list[int] = []
    indexing = False
    if vector is not None:
        current = (
            (model.model_key == vector.model_key)
            & (model.dimensions == len(vector.vector))
            & (model.embedded_hash == model.content_hash)
        )
        # 先物化兼容空间，避免 PostgreSQL 重排谓词后对不同维度计算距离。
        candidates = (
            select(model.record_id, model.embedding)
            .where(*scope, current)
            .cte("search_vectors")
            .prefix_with("MATERIALIZED")
        )
        dense = list(
            await db.scalars(
                select(candidates.c.record_id)
                .order_by(candidates.c.embedding.cosine_distance(vector.vector), candidates.c.record_id.desc())
                .limit(limit * 2)
            )
        )
        indexing = bool(await db.scalar(select(exists().where(*scope, or_(model.embedding.is_(None), ~current)))))
    scores = fuse_rankings(dense, sparse)
    identifiers = sorted(scores, key=lambda key: (scores[key], key), reverse=True)[:limit]
    return identifiers, SearchStatus(mode="hybrid" if vector is not None and dense else "keyword", indexing=indexing)
