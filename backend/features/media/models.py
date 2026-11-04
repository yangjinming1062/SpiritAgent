from datetime import datetime

from infrastructure.job_models import JobRecord
from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class ImageGenJob(JobRecord):
    __tablename__ = "image_gen_jobs"
    __mapper_args__ = {"polymorphic_identity": "media.image"}

    id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    params_json: Mapped[dict] = mapped_column(JSONB)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    bindings_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    cleanup_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaReviewAttempt(ModelBase, TimestampMixin):
    __tablename__ = "media_review_attempts"
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uq_media_review_request"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    basis_id: Mapped[str] = mapped_column(String(64))
    slot: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="running")
    execution_key: Mapped[str] = mapped_column(String(36))
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class MediaContinuation(ModelBase, TimestampMixin):
    __tablename__ = "media_continuations"
    __table_args__ = (UniqueConstraint("user_id", "operation_id", name="uq_media_continuation_operation"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    operation_id: Mapped[str] = mapped_column(String(256))
    owner_kind: Mapped[str] = mapped_column(String(48))
    owner_job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    dependency_job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(24), default="waiting")
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    context_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class MediaManualOperation(ModelBase, TimestampMixin):
    __tablename__ = "media_manual_operations"
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uq_media_manual_request"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    slot: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_json: Mapped[dict] = mapped_column(JSONB)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class VideoGenJob(JobRecord):
    """视频生成后台行；result_unknown 表示非幂等提交可能已被供应商接受，禁止自动重试。"""

    __tablename__ = "video_gen_jobs"
    __table_args__ = (UniqueConstraint("owner_id", "media_id", name="uq_video_jobs_owner_media"),)
    __mapper_args__ = {"polymorphic_identity": "media.video"}

    id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)

    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    structured_reply: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    media_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reply_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 历史修改先同事务失效；后台只处理撤销和清理，不再制作或交付。
    cleanup_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text)
    params_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    review_result_json: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_attempt_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
