"""内容的关系结构；动态和日记共享持久实体。"""

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class CompanionEntry(ModelBase, TimestampMixin):
    __tablename__ = "companion_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_entries_user_id"),
        UniqueConstraint("user_id", "source_key", name="uq_entries_source"),
        CheckConstraint("kind IN ('post', 'diary')", name="entry_kind"),
        CheckConstraint("content_type IN ('text', 'image', 'video', 'audio')", name="entry_content_type"),
        Index("ix_entries_timeline", "user_id", "kind", text("published_at DESC"), text("id DESC")),
        Index("ix_entries_unread", "user_id", "kind", postgresql_where=text("is_read IS FALSE")),
        Index(
            "uq_entries_diary_date", "user_id", "activity_date", unique=True, postgresql_where=text("kind = 'diary'")
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_key: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    kind: Mapped[str] = mapped_column(String(16))
    activity_date: Mapped[date] = mapped_column(Date)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    content_type: Mapped[str] = mapped_column(String(16), default="text", server_default=text("'text'"))
    title: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"))
    body: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class EntryLink(ModelBase):
    __tablename__ = "entry_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "source_id"],
            ["companion_entries.user_id", "companion_entries.id"],
            ondelete="CASCADE",
            name="fk_entry_links_source",
        ),
        ForeignKeyConstraint(
            ["user_id", "target_id"],
            ["companion_entries.user_id", "companion_entries.id"],
            ondelete="CASCADE",
            name="fk_entry_links_target",
        ),
        UniqueConstraint("source_id", "target_id", "kind", name="uq_entry_link"),
        CheckConstraint("position >= 0 AND source_id != target_id", name="entry_link_position"),
        Index("ix_entry_links_target", "target_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32), default="diary_source", server_default=text("'diary_source'"))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class EntryAsset(ModelBase):
    __tablename__ = "entry_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "entry_id"],
            ["companion_entries.user_id", "companion_entries.id"],
            ondelete="CASCADE",
            name="fk_entry_assets_entry",
        ),
        ForeignKeyConstraint(
            ["user_id", "asset_id"],
            ["assets.user_id", "assets.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_entry_assets_asset",
        ),
        UniqueConstraint("entry_id", "role", "position", name="uq_entry_asset_role"),
        CheckConstraint("position >= 0", name="entry_asset_position"),
        Index("ix_entry_assets_asset", "asset_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entry_id: Mapped[int] = mapped_column(BigInteger)
    asset_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class EntryComment(ModelBase, TimestampMixin):
    __tablename__ = "entry_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "post_id"],
            ["companion_entries.user_id", "companion_entries.id"],
            ondelete="CASCADE",
            name="fk_entry_comments_entry",
        ),
        ForeignKeyConstraint(
            ["user_id", "post_id", "reply_to_comment_id"],
            ["entry_comments.user_id", "entry_comments.post_id", "entry_comments.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_entry_comments_reply",
        ),
        UniqueConstraint("user_id", "post_id", "id", name="uq_entry_comment_scope"),
        UniqueConstraint("user_id", "source_key", name="uq_entry_comment_source"),
        UniqueConstraint("reply_to_comment_id", name="uq_entry_comment_reply"),
        CheckConstraint("role IN ('user', 'companion')", name="entry_comment_role"),
        CheckConstraint("(role = 'companion') = (reply_status = 'none')", name="entry_comment_delivery"),
        CheckConstraint(
            "reply_status IN ('none', 'pending', 'running', 'completed', 'failed')", name="entry_comment_state"
        ),
        CheckConstraint("reply_attempts >= 0", name="entry_comment_attempts"),
        Index("ix_entry_comments_order", "post_id", "created_at", "id"),
        Index(
            "ix_entry_comments_pending",
            "user_id",
            "post_id",
            postgresql_where=text("role = 'user' AND reply_status IN ('pending', 'running')"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    post_id: Mapped[int] = mapped_column(BigInteger)
    source_key: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    reply_to_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reply_status: Mapped[str] = mapped_column(String(16), default="none", server_default=text("'none'"))
    reply_error: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reply_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
