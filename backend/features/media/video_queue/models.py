"""远端尝试与密钥队列持久状态；业务删除不能级联释放远端占用。"""

from datetime import datetime
from uuid import uuid4

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class VideoQueueLane(ModelBase, TimestampMixin):
    __tablename__ = "video_queue_lanes"
    __table_args__ = (UniqueConstraint("provider", "credential_fingerprint", name="uq_video_queue_credential"),)

    token: Mapped[str] = mapped_column(String(32), unique=True, default=lambda: uuid4().hex)
    provider: Mapped[str] = mapped_column(String(64))
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoQueueRequest(ModelBase, TimestampMixin):
    __tablename__ = "video_queue_requests"
    __table_args__ = (
        UniqueConstraint("generation_id", "attempt_index", name="uq_video_queue_attempt"),
        Index("ix_video_queue_fifo", "lane_id", "state", "enqueued_at", "id"),
        Index("ix_video_queue_owner", "kind", "entity_id", "generation_id"),
    )

    token: Mapped[str] = mapped_column(String(32), unique=True, default=lambda: uuid4().hex)
    lane_id: Mapped[int] = mapped_column(ForeignKey("video_queue_lanes.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_id: Mapped[str] = mapped_column(String(64))
    attempt_index: Mapped[int] = mapped_column(Integer)
    frozen_json: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_limit_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
