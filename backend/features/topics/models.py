"""按账户与陪伴预设隔离的每日采集、原始来源、分享记录及反馈。"""

from datetime import date, datetime
from typing import Any

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column


class TopicBatch(ModelBase, TimestampMixin):
    __tablename__ = "companion_topic_batches"
    __table_args__ = (
        UniqueConstraint("user_id", "system_preset_id", "activity_date", name="uq_topic_batch_scope_date"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    system_preset_id: Mapped[str] = mapped_column(String(32))
    activity_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="collecting", server_default=text("'collecting'"))
    search_requests: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    detail_requests: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(160), nullable=True)


class CompanionTopic(ModelBase, TimestampMixin):
    __tablename__ = "companion_topics"
    __table_args__ = (
        UniqueConstraint("user_id", "system_preset_id", "platform", "source_id", name="uq_topic_scope_source"),
        Index("ix_companion_topics_batch_id", "batch_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    system_preset_id: Mapped[str] = mapped_column(String(32))
    batch_id: Mapped[int] = mapped_column(ForeignKey("companion_topic_batches.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(96))
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(160))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_type: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    subtitle: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    user_relevance: Mapped[str] = mapped_column(Text)
    companion_interest: Mapped[str] = mapped_column(Text)
    conversation_angle: Mapped[str] = mapped_column(Text)
    event_key: Mapped[str] = mapped_column(String(200))


class TopicUsage(ModelBase, TimestampMixin):
    __tablename__ = "companion_topic_usages"
    __table_args__ = (UniqueConstraint("topic_id", "message_id", name="uq_topic_usage_message"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    system_preset_id: Mapped[str] = mapped_column(String(32))
    topic_id: Mapped[int] = mapped_column(ForeignKey("companion_topics.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class TopicFeedback(ModelBase, TimestampMixin):
    __tablename__ = "companion_topic_feedback"
    __table_args__ = (UniqueConstraint("topic_id", name="uq_topic_feedback_topic"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    system_preset_id: Mapped[str] = mapped_column(String(32))
    topic_id: Mapped[int] = mapped_column(ForeignKey("companion_topics.id", ondelete="CASCADE"), index=True)
    value: Mapped[str | None] = mapped_column(String(8), nullable=True)
