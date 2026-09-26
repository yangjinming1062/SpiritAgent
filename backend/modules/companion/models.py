from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# 场景 / 外观政策默认值。
SCENE_POLICY_DEFAULT: str = "llm_may_replace"
OUTFIT_POLICY_DEFAULT: str = "llm_may_replace"

if TYPE_CHECKING:
    from modules.auth import User


class CompanionOutfit(ModelBase, TimestampMixin):
    """衣橱外观及其着装参考；ready 表示参考就绪，视频状态由动作包维护。"""

    __tablename__ = "companion_outfits"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="新外观")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 全身立绘裸路径（草稿期 temp-media/，确认后 companion-avatars/），读取时重签名
    fullbody_url: Mapped[str] = mapped_column(String(2048), default="")
    # draft → ready | failed | expired
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"), index=True)
    # 审计：用户着装描述 / feedback / 参考图前缀标记，仿 AvatarAsset.prompt_json
    source_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    is_initial: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    # 与首次视频包同事务置位；删除视频包不撤销已启动事实。
    initial_video_started: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    initial_video_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


class Persona(ModelBase, TimestampMixin):
    """伙伴人设主表：definition_json 存原始字段定义，运行期按 session language 实时渲染。"""

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    appearance_parts_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
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
    # 锁定仅限制自主新增与切换。
    scene_policy: Mapped[str] = mapped_column(
        String(16),
        default=SCENE_POLICY_DEFAULT,
        server_default=text(f"'{SCENE_POLICY_DEFAULT}'"),
    )
    scene_switch_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    scene_state_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # 外观政策：llm_may_replace（默认，夜间可自主换装 / 添置外观）/ locked（仅用户可操作）。
    outfit_policy: Mapped[str] = mapped_column(
        String(16),
        default=OUTFIT_POLICY_DEFAULT,
        server_default=text(f"'{OUTFIT_POLICY_DEFAULT}'"),
    )
    # 当前心境：由独立状态推理更新的最新心理活动说明，端到端投射至客户端身份轨展示。
    current_mood: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    user: Mapped["User"] = relationship(back_populates="persona")


class AvatarAsset(ModelBase):
    """asset_url 存在 companion-avatars/（持久）以让重新登录跨过 24h temp-media TTL。"""

    __tablename__ = "avatar_assets"
    # 部分唯一索引（每用户一个 active）位于 alembic baseline——需要 WHERE 子句。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    style: Mapped[str] = mapped_column(String(64), default="")
    # 日常出镜的全身参考；锁定身份后仍可重绘。
    seed_fullbody_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    # 锁定身份的持久标志，不能由可重绘的种子路径推断。
    is_fullbody_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")


class FullbodyCandidate(ModelBase, TimestampMixin):
    """已确认角色的待采纳全身图；采纳前不改变当前身份。"""

    __tablename__ = "companion_fullbody_candidates"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatar_assets.id", ondelete="CASCADE"), index=True)
    base_fullbody_url: Mapped[str] = mapped_column(String(2048))
    base_revision: Mapped[int] = mapped_column(Integer)
    image_url: Mapped[str] = mapped_column(String(2048))
    body_features_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    body_source_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CompanionMediaReview(ModelBase, TimestampMixin):
    """出镜媒体正式交付前的用户核对记录。"""

    __tablename__ = "companion_media_reviews"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_type: Mapped[str] = mapped_column(String(8))
    media_url: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    publication: Mapped[dict | None] = mapped_column(JSON, nullable=True)
