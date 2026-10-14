from typing import TYPE_CHECKING, Any

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    JSON,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    pass

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from features.accounts.models import User


class UserModelConfig(ModelBase, TimestampMixin):
    __tablename__ = "user_model_configs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    ai_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    user: Mapped["User"] = relationship(back_populates="model_config")
