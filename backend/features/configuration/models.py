from typing import TYPE_CHECKING, Any

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    ForeignKey,
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
