from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class Memory(ModelBase, TimestampMixin):
    __tablename__ = "memories"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    user: Mapped["User"] = relationship(back_populates="memories")
