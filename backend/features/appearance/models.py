from datetime import datetime
from typing import TYPE_CHECKING, Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.job_models import JobRecord
from infrastructure.models import ModelBase, TimestampMixin
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    # 生成时的角色卡修订与全身身份图路径；确认据此判断外观是否仍对应当前身份。
    character_card_revision: int | None = None
    identity_reference_path: str = Field(min_length=1)

    @field_validator("reference_image_path", "reference_description")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return value or None

    def dump(self) -> dict[str, Any]:
        """写入来源及身份守卫，省略空值。"""
        return self.model_dump(mode="json", exclude_none=True)


class OutfitCreationJob(JobRecord):
    __tablename__ = "outfit_creation_jobs"
    __mapper_args__ = {"polymorphic_identity": "outfit.create"}
    id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(Text)
    result_outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id", ondelete="SET NULL"))


class CompanionOutfit(ModelBase, TimestampMixin):
    """衣柜造型素材及其着装参考；ready 表示静态造型图就绪。"""

    __tablename__ = "companion_outfits"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="新外观")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    description_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 全身立绘裸路径（草稿期 temp-media/，确认后 companion-assets/{user_id}/），读取时重签名
    fullbody_url: Mapped[str] = mapped_column(String(2048), default="")
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    # draft → ready | expired
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default=text("'draft'"), index=True)
    # 生成来源与身份修订守卫，结构见 OutfitSource
    source_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    is_initial: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))


class AvatarAsset(ModelBase, TimestampMixin):
    """头像与全身参考保存裸路径；引导草稿可指向 temp-media/。"""

    __tablename__ = "avatar_assets"
    __table_args__ = (Index("uq_avatar_assets_one_active", "user_id", unique=True, postgresql_where=text("active")),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    asset_url: Mapped[str] = mapped_column(String(2048))
    portrait_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    fullbody_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    generation_origin_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    fullbody_generation_origin_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 日常出镜的全身参考；锁定身份后仍可重绘。
    seed_fullbody_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    # 身份锁定的持久标志，不能由可重绘的种子路径推断。
    is_fullbody_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    automatic_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    overrides_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    analysis_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("character_analysis_jobs.id", ondelete="SET NULL", use_alter=True)
    )
    analysis_job: Mapped["CharacterAnalysisJob | None"] = relationship(
        foreign_keys=[analysis_job_id], lazy="joined", post_update=True
    )

    user: Mapped["User"] = relationship(back_populates="avatar_assets")


class FullbodyCandidate(ModelBase, TimestampMixin):
    """已确认角色的待采纳全身图；采纳前不改变当前身份。"""

    __tablename__ = "companion_fullbody_candidates"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatar_assets.id", ondelete="CASCADE"), index=True)
    base_fullbody_url: Mapped[str] = mapped_column(String(2048))
    base_revision: Mapped[int] = mapped_column(Integer)
    image_url: Mapped[str] = mapped_column(String(2048))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    base_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    generation_origin_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    body_features_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppearanceAsset(ModelBase):
    __tablename__ = "appearance_assets"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(avatar_id, outfit_id, candidate_id, analysis_job_id) = 1", name="appearance_asset_owner"
        ),
        UniqueConstraint("avatar_id", "asset_id", name="uq_avatar_asset_binding"),
        UniqueConstraint("outfit_id", "asset_id", name="uq_outfit_asset_binding"),
        UniqueConstraint("candidate_id", "asset_id", name="uq_candidate_asset_binding"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int | None] = mapped_column(ForeignKey("avatar_assets.id", ondelete="CASCADE"), index=True)
    outfit_id: Mapped[int | None] = mapped_column(ForeignKey("companion_outfits.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("companion_fullbody_candidates.id", ondelete="CASCADE"), index=True
    )
    analysis_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("character_analysis_jobs.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)


class CharacterAnalysisJob(JobRecord):
    __tablename__ = "character_analysis_jobs"
    __mapper_args__ = {"polymorphic_identity": "character_analysis"}
    id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    avatar_id: Mapped[int | None] = mapped_column(ForeignKey("avatar_assets.id", ondelete="SET NULL"), index=True)
    extraction_id: Mapped[str] = mapped_column(String(36), default="")
    portrait_status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    body_status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    portrait_result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    body_result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    portrait_source_path: Mapped[str] = mapped_column(String(2048), default="")
    body_source_path: Mapped[str] = mapped_column(String(2048), default="")
