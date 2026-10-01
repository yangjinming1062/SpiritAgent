"""独立动态、评论线程和发布任务。"""

from datetime import date, datetime
from enum import StrEnum
from uuid import uuid4

from common import ModelBase, TimestampMixin
from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class PostContentType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class PostCommentRole(StrEnum):
    USER = "user"
    COMPANION = "companion"


class CompanionPost(ModelBase, TimestampMixin):
    __tablename__ = "companion_posts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    activity_date: Mapped[date] = mapped_column(Date, index=True)
    content_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    audio_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    quota_kind: Mapped[str] = mapped_column(String(24), index=True)
    comments: Mapped[list["CompanionPostComment"]] = relationship(
        lazy="selectin",
        order_by="(CompanionPostComment.created_at, CompanionPostComment.id)",
        foreign_keys="CompanionPostComment.post_id",
    )


class CompanionPostComment(ModelBase, TimestampMixin):
    __tablename__ = "companion_post_comments"
    __table_args__ = (UniqueConstraint("reply_to_comment_id", name="uq_post_comment_reply_target"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    post_id: Mapped[str] = mapped_column(ForeignKey("companion_posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    reply_to_comment_id: Mapped[str | None] = mapped_column(
        ForeignKey("companion_post_comments.id", ondelete="SET NULL", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    reply_status: Mapped[str] = mapped_column(String(16), default="none", server_default=text("'none'"), index=True)
    reply_error: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reply_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class PostPublication(ModelBase, TimestampMixin):
    __tablename__ = "post_publications"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_post_publication_request"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    trigger: Mapped[str] = mapped_column(String(24))
    quota_kind: Mapped[str] = mapped_column(String(24))
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    activity_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(32), default="planning")
    request_json: Mapped[dict] = mapped_column(JSON)
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict)
    post_id: Mapped[str | None] = mapped_column(ForeignKey("companion_posts.id", ondelete="SET NULL"), nullable=True)
    error: Mapped[str | None] = mapped_column(String(160), nullable=True)
