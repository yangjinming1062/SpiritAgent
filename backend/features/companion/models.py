from datetime import datetime
from typing import TYPE_CHECKING

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from features.companion.policy_contracts import OUTFIT_POLICY_DEFAULT

if TYPE_CHECKING:
    from features.accounts.models import User


SCENE_POLICY_DEFAULT: str = "llm_may_replace"


class Persona(ModelBase, TimestampMixin):
    """伙伴人设主表：definition_json 存原始字段定义，运行期按 session language 实时渲染。"""

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    personality_tags_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    is_portrait_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("FALSE"),
        index=True,
    )
    portrait_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # locked 仅限制自主添置环境素材。
    scene_policy: Mapped[str] = mapped_column(
        String(16),
        default=SCENE_POLICY_DEFAULT,
        server_default=text(f"'{SCENE_POLICY_DEFAULT}'"),
    )
    # llm_may_replace=可自主添置造型素材；locked=仅用户可操作。
    outfit_policy: Mapped[str] = mapped_column(
        String(16),
        default=OUTFIT_POLICY_DEFAULT,
        server_default=text(f"'{OUTFIT_POLICY_DEFAULT}'"),
    )
    user: Mapped["User"] = relationship(back_populates="persona")
