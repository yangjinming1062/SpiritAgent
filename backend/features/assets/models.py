from datetime import datetime

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column


class Asset(ModelBase, TimestampMixin):
    """正式文件登记及可重试回收状态；业务关联负责决定是否仍在使用。"""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_assets_user_id"),
        UniqueConstraint("user_id", "path", name="uq_assets_user_path"),
        CheckConstraint("state IN ('ready', 'cleanup', 'deleted')", name="asset_state"),
        CheckConstraint("size_bytes >= 0 AND attempts >= 0", name="asset_numbers"),
        Index("ix_assets_cleanup", "not_before", postgresql_where=text("state = 'cleanup'")),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(
        String(128), default="application/octet-stream", server_default="application/octet-stream"
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="ready", server_default="ready")
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
