from datetime import datetime

from common import ModelBase
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column


class VideoGenJob(ModelBase):
    """视频生成后台行；result_unknown 表示非幂等提交可能已被供应商接受，禁止自动重试。"""

    __tablename__ = "video_gen_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="minimax")
    model: Mapped[str] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(Text)
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # 行插入与 submit 完成之间的短暂窗口必须可空；轮询任务首轮用 MiniMax 返回的 task_id 回填。
    provider_task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
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
