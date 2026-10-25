from typing import TYPE_CHECKING, Any

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from features.accounts.models import User


class UserSetting(ModelBase, TimestampMixin):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    setting_key: Mapped[str] = mapped_column(String(128), index=True)
    setting_value: Mapped[Any] = mapped_column(JSONB(none_as_null=False))

    user: Mapped["User"] = relationship(back_populates="settings")


class SystemSetting(ModelBase, TimestampMixin):
    __tablename__ = "system_settings"
    source_key = None

    setting_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    setting_value: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
