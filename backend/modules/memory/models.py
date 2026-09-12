from typing import TYPE_CHECKING, Any

from common import ModelBase, TimestampMixin
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class Memory(ModelBase, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (CheckConstraint("content_version > 0", name="ck_memories_content_version"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    system_preset_id: Mapped[str] = mapped_column(String(32))
    source_kind: Mapped[str] = mapped_column(String(32))
    source_refs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    content: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    user: Mapped["User"] = relationship(back_populates="memories")
