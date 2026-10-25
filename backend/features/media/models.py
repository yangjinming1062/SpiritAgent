from datetime import datetime

from infrastructure.job_models import JobRecord
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
    # 每次提交前清空；受理本次提交的供应商返回 task_id 后写入，未受理或提交结果未知时为空。
    provider_task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_attempt_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
