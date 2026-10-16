from datetime import datetime
from typing import TYPE_CHECKING

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.models import ModelBase, TimestampMixin
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from features.accounts.models import User


class OutfitSource(BaseModel):
    """着装设计来源、已接受反馈与生成时的身份修订守卫。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    description: str = ""
    generation_origin: GenerationOrigin | None = None
    reference_image_path: str | None = None
    reference_description: str | None = None
    feedback_history: list[str] = Field(default_factory=list)
    # 生成时的角色卡修订与全身身份图路径；确认、穿着和视频包据此判断外观是否仍对应当前身份。
    character_card_revision: int | None = None
    identity_reference_path: str | None = None

    @field_validator("reference_image_path", "reference_description", "identity_reference_path")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return value or None

    def dump(self) -> str:
        """写入来源及身份守卫，省略空值。"""
        return self.model_dump_json(exclude_none=True)


class CompanionOutfit(ModelBase, TimestampMixin):
    """衣柜外观及其着装参考；ready 表示参考就绪，视频状态由动作包维护。"""

    __tablename__ = "companion_outfits"
    __table_args__ = (
        Index("uq_companion_outfits_one_active", "user_id", unique=True, postgresql_where=text("active")),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="新外观")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    description_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 全身立绘裸路径（草稿期 temp-media/，确认后 companion-assets/{user_id}/），读取时重签名
    fullbody_url: Mapped[str] = mapped_column(String(2048), default="")
    # draft → ready | expired
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"), index=True)
    # 生成来源与身份修订守卫，结构见 OutfitSource
    source_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    is_initial: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    # 每套着装首次动作包同事务置位；删除或备份恢复后不自动补建。
    initial_video_started: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    initial_video_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)


class AvatarAsset(ModelBase):
    """头像与全身参考保存裸路径；引导草稿可指向 temp-media/。"""

    __tablename__ = "avatar_assets"
    __table_args__ = (Index("uq_avatar_assets_one_active", "user_id", unique=True, postgresql_where=text("active")),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    generation_origin_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fullbody_generation_origin_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 日常出镜的全身参考；锁定身份后仍可重绘。
    seed_fullbody_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    # 身份锁定的持久标志，不能由可重绘的种子路径推断。
    is_fullbody_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
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
    generation_origin_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_features_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
