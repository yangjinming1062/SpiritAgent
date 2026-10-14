from typing import TYPE_CHECKING

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    JSON,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    pass


class CompanionMediaReview(ModelBase, TimestampMixin):
    """出镜媒体正式交付前的用户核对记录。"""

    __tablename__ = "companion_media_reviews"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_type: Mapped[str] = mapped_column(String(8))
    media_url: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    publication: Mapped[dict | None] = mapped_column(JSON, nullable=True)
