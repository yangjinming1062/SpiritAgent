"""记忆当前内容、证据、必要修订、遗忘指纹和独立向量。"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from infrastructure.models import ModelBase, TimestampMixin
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from features.accounts.models import User

USER_PROFILE_MAX_CONTENT_CHARS = 2000
MEMORY_REVISION_LIMIT = 20


class Memory(ModelBase, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_memories_owner_id"),
        UniqueConstraint("user_id", "source_key", name="uq_memories_source_key"),
        UniqueConstraint("user_id", "system_preset_id", "source_event_id", name="uq_memories_source_event"),
        ForeignKeyConstraint(
            ["user_id", "source_event_id"],
            ["life_events.user_id", "life_events.id"],
            ondelete="SET NULL (source_event_id)",
            name="fk_memories_source_event",
        ),
        UniqueConstraint("user_id", "system_preset_id", "kind", "slot_key", name="uq_memories_scope_slot"),
        ForeignKeyConstraint(
            ["user_id", "source_conversation_id"],
            ["conversations.user_id", "conversations.id"],
            ondelete="SET NULL (source_conversation_id)",
            name="fk_memories_source_conversation",
        ),
        CheckConstraint("content_version > 0", name="content_version"),
        CheckConstraint("source_event_id IS NULL OR system_preset_id = 'companion'", name="life_source_scope"),
        CheckConstraint("status IN ('candidate', 'active', 'invalidated', 'forgotten')", name="status"),
        CheckConstraint("basis IN ('explicit', 'inferred', 'observed', 'system')", name="basis"),
        CheckConstraint("usage IN ('contextual', 'background')", name="usage"),
        CheckConstraint("jsonb_typeof(tags) = 'array'", name="tags_array"),
        Index("ix_memories_scope_updated", "user_id", "system_preset_id", text("updated_at DESC"), text("id DESC")),
        Index("ix_memories_scope_kind", "user_id", "system_preset_id", "kind", "status"),
        Index(
            "ix_memories_content_trgm", "content", postgresql_using="gin", postgresql_ops={"content": "gin_trgm_ops"}
        ),
        Index(
            "ix_memories_context_trgm", "context", postgresql_using="gin", postgresql_ops={"context": "gin_trgm_ops"}
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_key: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    system_preset_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32), default="recall", server_default="recall")
    slot_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    source_conversation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_batch_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    basis: Mapped[str] = mapped_column(String(16), default="system", server_default="system")
    usage: Mapped[str] = mapped_column(String(16), default="contextual", server_default="contextual")
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="", server_default="")
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    importance: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    user: Mapped["User"] = relationship(back_populates="memories")


class MemoryEvidenceRow(ModelBase):
    __tablename__ = "memory_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "memory_id"],
            ["memories.user_id", "memories.id"],
            ondelete="CASCADE",
            name="fk_memory_evidence_owner",
        ),
        ForeignKeyConstraint(
            ["user_id", "message_id"],
            ["messages.user_id", "messages.id"],
            ondelete="SET NULL (message_id)",
            name="fk_memory_evidence_message_owner",
        ),
        UniqueConstraint("memory_id", "fingerprint", "quote", "stance", name="uq_memory_evidence_quote"),
        CheckConstraint("stance IN ('supports', 'opposes')", name="stance"),
        Index("ix_memory_evidence_memory", "memory_id"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    memory_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    quote: Mapped[str] = mapped_column(Text)
    stance: Mapped[str] = mapped_column(String(16))
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryRevision(ModelBase):
    __tablename__ = "memory_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "memory_id"],
            ["memories.user_id", "memories.id"],
            ondelete="CASCADE",
            name="fk_memory_revision_owner",
        ),
        UniqueConstraint("memory_id", "content_version", name="uq_memory_revision_version"),
        CheckConstraint("content_version > 0", name="revision_content_version"),
        Index("ix_memory_revisions_memory", "memory_id", "content_version"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    memory_id: Mapped[int] = mapped_column(BigInteger)
    content_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class MemoryFingerprint(ModelBase):
    __tablename__ = "memory_fingerprints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "memory_id"],
            ["memories.user_id", "memories.id"],
            ondelete="SET NULL (memory_id)",
            name="fk_memory_fingerprint_owner",
        ),
        UniqueConstraint("memory_id", "fingerprint", name="uq_memory_fingerprint_source"),
        Index(
            "ix_memory_fingerprints_suppressed",
            "user_id",
            "system_preset_id",
            "fingerprint",
            postgresql_where=text("suppressed"),
        ),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    system_preset_id: Mapped[str] = mapped_column(String(32))
    memory_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))


class MemoryEmbeddingRow(ModelBase):
    __tablename__ = "memory_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "memory_id"],
            ["memories.user_id", "memories.id"],
            ondelete="CASCADE",
            name="fk_memory_embedding_owner",
        ),
        UniqueConstraint("memory_id", "model_key", name="uq_memory_embedding_model"),
        CheckConstraint("dimensions > 0 AND vector_dims(embedding) = dimensions", name="dimensions"),
        CheckConstraint("content_version > 0", name="content_version"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    memory_id: Mapped[int] = mapped_column(BigInteger)
    model_key: Mapped[str] = mapped_column(String(64))
    dimensions: Mapped[int] = mapped_column(Integer)
    content_version: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector())
