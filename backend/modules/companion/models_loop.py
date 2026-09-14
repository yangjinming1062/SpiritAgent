from datetime import datetime

from common import ModelBase, TimestampMixin
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

COMPANION_CRON_SOURCE_PREFIX: str = "cron:"


def companion_cron_source_key(job_id: int) -> str:
    return f"{COMPANION_CRON_SOURCE_PREFIX}{job_id}"


class CompanionIntent(ModelBase, TimestampMixin):
    """待兑现的陪伴意图；等待、认领和交付状态与模型调用的生命周期分离。"""

    __tablename__ = "companion_intents"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    intent: Mapped[str] = mapped_column(Text)
    source_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="waiting", server_default=text("'waiting'"), index=True)
    not_before_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    wake_event: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
