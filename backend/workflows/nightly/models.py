from datetime import date, datetime
from typing import TYPE_CHECKING

from infrastructure.models import ModelBase
from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    pass


class NightlyActivityLog(ModelBase):
    __tablename__ = "nightly_activity_logs"
    __table_args__ = (UniqueConstraint("user_id", "target_date", name="uq_nightly_activity_logs_user_date"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", server_default=text("'running'"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class NightlyActivityAction(ModelBase):
    """夜间计划的持久化动作账本；可核对的在途子任务续跑，无法核对的动作终止且不重放。"""

    __tablename__ = "nightly_activity_actions"
    __table_args__ = (UniqueConstraint("log_id", "action_key", name="uq_nightly_activity_actions_log_key"),)

    log_id: Mapped[int] = mapped_column(ForeignKey("nightly_activity_logs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    action_key: Mapped[str] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(64), index=True)
    phase: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default=text("'pending'"), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'::json"))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
