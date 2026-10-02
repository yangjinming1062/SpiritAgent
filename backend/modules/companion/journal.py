"""伙伴每日日记。"""

from datetime import date
from uuid import uuid4

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class CompanionDiaryEntry(ModelBase, TimestampMixin):
    """伙伴发布的第一人称日记；日期唯一约束防止夜间恢复重复发布。"""

    __tablename__ = "companion_diary_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "entry_date", name="uq_companion_diary_user_date"),
        Index("ix_companion_diary_unread_user", "user_id", postgresql_where=text("is_read IS FALSE")),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(
        String(128),
        default="",
        server_default=text("''"),
    )
    body: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    mood: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    post_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'"),
    )
