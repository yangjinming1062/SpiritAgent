from datetime import datetime
from typing import TYPE_CHECKING, Any

from common import ModelBase, TimestampMixin
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User

# memories.embedding 列宽的唯一事实源；更换维度须同步迁移
MEMORY_EMBEDDING_DIM = 1536
USER_PROFILE_MAX_CONTENT_CHARS = 2000
# 每个作用域内同一 context 只有一行的槽位记忆：部分唯一索引名 → context 前缀。索引按此声明，`upsert_slotted_memory` 的 ON CONFLICT 谓词使用同一前缀；增减须同步迁移。
_MEMORY_SLOT_INDEXES: dict[str, str] = {
    "uq_memories_user_context": "user_profile:",
    "uq_memories_diary_day": "diary:",
    "uq_memories_reflection_slot": "reflection:",
    "uq_memories_interaction_day": "interaction_stats:",
    "uq_memories_nightly_actions": "recall:nightly_actions:",
}
MEMORY_SLOT_CONTEXT_PREFIXES: tuple[str, ...] = tuple(_MEMORY_SLOT_INDEXES.values())


class Memory(ModelBase, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("content_version > 0", name="ck_memories_content_version"),
        CheckConstraint("status IN ('candidate', 'active', 'invalidated', 'forgotten')", name="ck_memories_status"),
        CheckConstraint("basis IN ('explicit', 'inferred', 'observed', 'system')", name="ck_memories_basis"),
        CheckConstraint("usage IN ('contextual', 'background')", name="ck_memories_usage"),
        *(
            Index(
                name,
                "user_id",
                "system_preset_id",
                "context",
                unique=True,
                postgresql_where=text(f"context LIKE '{prefix}%'"),
            )
            for name, prefix in _MEMORY_SLOT_INDEXES.items()
        ),
        Index("ix_memories_scope_updated", "user_id", "system_preset_id", text("updated_at DESC"), text("id DESC")),
        Index(
            "ix_memories_recall_user_updated",
            "user_id",
            text("updated_at DESC"),
            postgresql_where=text("context LIKE 'recall:%'"),
        ),
        Index(
            "ix_memories_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_memories_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
        Index(
            "ix_memories_context_trgm",
            "context",
            postgresql_using="gin",
            postgresql_ops={"context": "gin_trgm_ops"},
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    system_preset_id: Mapped[str] = mapped_column(String(32))
    source_kind: Mapped[str] = mapped_column(String(32))
    source_refs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    basis: Mapped[str] = mapped_column(String(16), default="system", server_default="system")
    usage: Mapped[str] = mapped_column(String(16), default="contextual", server_default="contextual")
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(MEMORY_EMBEDDING_DIM), nullable=True)

    user: Mapped["User"] = relationship(back_populates="memories")
