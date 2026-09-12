from datetime import datetime
from typing import TYPE_CHECKING, Any

from common import ModelBase, TimestampMixin
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class Memory(ModelBase, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("content_version > 0", name="ck_memories_content_version"),
        CheckConstraint("status IN ('candidate', 'active', 'invalidated', 'forgotten')", name="ck_memories_status"),
        CheckConstraint("basis IN ('explicit', 'inferred', 'observed', 'system')", name="ck_memories_basis"),
        CheckConstraint("usage IN ('contextual', 'background')", name="ck_memories_usage"),
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
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    user: Mapped["User"] = relationship(back_populates="memories")
