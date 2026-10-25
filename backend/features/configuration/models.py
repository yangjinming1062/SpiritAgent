from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from infrastructure.models import ModelBase, TimestampMixin
from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from features.accounts.models import User


class UserModelConfig(ModelBase, TimestampMixin):
    __tablename__ = "user_model_configs"
    id = None

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ai_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    user: Mapped["User"] = relationship(back_populates="model_config")


class UserSocialAccount(ModelBase, TimestampMixin):
    __tablename__ = "user_social_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_user_social_accounts_platform"),
        CheckConstraint("state IN ('unconfigured', 'configured', 'login_required')", name="social_account_state"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(32))
    credentials: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    revision: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    state: Mapped[str] = mapped_column(String(24), default="unconfigured")
    login_revision: Mapped[str | None] = mapped_column(String(36))
    login_session_id: Mapped[str | None] = mapped_column(String(96))
    login_state: Mapped[str | None] = mapped_column(String(32))
    login_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    login_verification_error: Mapped[str | None] = mapped_column(String(24))
