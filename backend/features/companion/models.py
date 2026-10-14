from datetime import datetime
from typing import TYPE_CHECKING

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
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
    # 当前环境仅由成功启用的场景确定。
    active_scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_scenes.id", ondelete="SET NULL"),
        nullable=True,
    )
    # locked 仅限制自主新增与切换。
    scene_policy: Mapped[str] = mapped_column(
        String(16),
        default=SCENE_POLICY_DEFAULT,
        server_default=text(f"'{SCENE_POLICY_DEFAULT}'"),
    )
    scene_switch_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    scene_state_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # llm_may_replace=夜间可自主换装/添置；locked=仅用户可操作。
    outfit_policy: Mapped[str] = mapped_column(
        String(16),
        default=OUTFIT_POLICY_DEFAULT,
        server_default=text(f"'{OUTFIT_POLICY_DEFAULT}'"),
    )
    # 当前心境：独立状态推理产出的心理活动说明，投射到客户端身份轨。
    current_mood: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    user: Mapped["User"] = relationship(back_populates="persona")
