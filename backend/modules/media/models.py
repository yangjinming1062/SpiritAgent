from datetime import datetime

from common import ModelBase
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column


class VideoGenJob(ModelBase):
    """视频生成后台行；result_unknown 表示非幂等提交可能已被供应商接受，禁止自动重试。"""

    __tablename__ = "video_gen_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structured_reply: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    media_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
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
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # 每次提交前清空；受理本次提交的供应商返回 task_id 后写入，未受理或提交结果未知时为空。
    provider_task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_state_json: Mapped[str] = mapped_column(Text)
    generation_attempt_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_video_gen_jobs_user_status", "user_id", "status"),)
