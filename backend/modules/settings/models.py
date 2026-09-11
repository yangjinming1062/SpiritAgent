from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class UserSetting(ModelBase, TimestampMixin):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    setting_key: Mapped[str] = mapped_column(String(128), index=True)
    setting_value: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship(back_populates="settings")


class SystemSetting(ModelBase, TimestampMixin):
    __tablename__ = "system_settings"

    setting_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    setting_value: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
