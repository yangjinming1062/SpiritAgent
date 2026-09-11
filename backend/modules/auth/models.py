from datetime import datetime
from typing import TYPE_CHECKING, Any

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.companion import AvatarAsset, Persona
    from modules.conversation import Conversation
    from modules.memory import Memory
    from modules.scheduler import CronJob
    from modules.settings import UserSetting


class User(ModelBase, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    activation_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    activation_token_hash: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    nightly_activity_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))

    login_records: Mapped[list["LoginRecord"]] = relationship(back_populates="user", passive_deletes=True)
    model_config: Mapped["UserModelConfig | None"] = relationship(
        back_populates="user",
        uselist=False,
        passive_deletes=True,
    )
    persona: Mapped["Persona | None"] = relationship(back_populates="user", uselist=False, passive_deletes=True)
    avatar_assets: Mapped[list["AvatarAsset"]] = relationship(back_populates="user", passive_deletes=True)
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user", passive_deletes=True)
    cron_jobs: Mapped[list["CronJob"]] = relationship(back_populates="user", passive_deletes=True)
    settings: Mapped[list["UserSetting"]] = relationship(back_populates="user", passive_deletes=True)
    memories: Mapped[list["Memory"]] = relationship(back_populates="user", passive_deletes=True)


class LoginRecord(ModelBase):
    __tablename__ = "login_records"
    __table_args__ = (UniqueConstraint("token_jti", name="uq_login_records_token_jti"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_jti: Mapped[str] = mapped_column(String(64), index=True)
    client_version: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    logout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="login_records")


# Admin token 携带 DB 后端的 jti，便于疑似密钥泄露时设 is_active=False 强制吊销。
class AdminSession(ModelBase):
    __tablename__ = "admin_sessions"
    __table_args__ = (UniqueConstraint("token_jti", name="uq_admin_sessions_token_jti"),)

    token_jti: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    client_version: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserModelConfig(ModelBase, TimestampMixin):
    __tablename__ = "user_model_configs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    ai_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship(back_populates="model_config")
