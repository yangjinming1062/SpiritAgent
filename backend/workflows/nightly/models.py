from datetime import date, datetime

from infrastructure.models import ModelBase
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class NightlyActivityLog(ModelBase):
    __tablename__ = "nightly_activity_logs"
    __table_args__ = (UniqueConstraint("user_id", "target_date", name="uq_nightly_activity_logs_user_date"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", server_default=text("'running'"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NightlyActivityAction(ModelBase):
    """夜间计划项；实际执行状态与结果只由关联生活步骤持有。"""

    __tablename__ = "nightly_activity_actions"
    __table_args__ = (
        UniqueConstraint("log_id", "action_key", name="uq_nightly_activity_actions_log_key"),
        UniqueConstraint("life_step_id", name="uq_nightly_activity_actions_step"),
        CheckConstraint("status IN ('pending', 'skipped', 'blocked', 'failed')", name="plan_status"),
        CheckConstraint("life_step_id IS NULL OR (status = 'pending' AND reason IS NULL)", name="step_owns_execution"),
    )

    log_id: Mapped[int] = mapped_column(ForeignKey("nightly_activity_logs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)
    life_step_id: Mapped[int | None] = mapped_column(ForeignKey("life_steps.id", ondelete="NO ACTION"))
    action_key: Mapped[str] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(64), index=True)
    phase: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default=text("'pending'"), index=True)
    arguments: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
