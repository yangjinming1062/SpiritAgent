"""已确认形象的固定特征及可恢复的双图分析任务。"""

from typing import Annotated, Literal

from common import ModelBase, TimestampMixin
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

FeatureText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
ExtractionStatus = Literal["pending", "running", "ready", "failed"]


class PortraitFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head_shape: FeatureText = ""
    facial_features: FeatureText = ""
    facial_surface: FeatureText = ""
    head_identifiers: FeatureText = ""


class BodyFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_shape: FeatureText = ""
    proportions: FeatureText = ""
    limbs_and_appendages: FeatureText = ""
    body_surface: FeatureText = ""


class CharacterFeatures(PortraitFeatures, BodyFeatures):
    pass


class CharacterOverrides(BaseModel):
    """None 恢复自动值，空字符串是用户明确清空。"""

    model_config = ConfigDict(extra="forbid")

    head_shape: FeatureText | None = None
    facial_features: FeatureText | None = None
    facial_surface: FeatureText | None = None
    head_identifiers: FeatureText | None = None
    body_shape: FeatureText | None = None
    proportions: FeatureText | None = None
    limbs_and_appendages: FeatureText | None = None
    body_surface: FeatureText | None = None


class CharacterCardSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    avatar_id: int
    revision: int
    features: CharacterFeatures
    overrides: CharacterOverrides


class CharacterCardResponse(CharacterCardSnapshot):
    automatic: CharacterFeatures
    status: ExtractionStatus
    portrait_status: ExtractionStatus
    body_status: ExtractionStatus
    error: str | None


class CharacterCardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_avatar_id: int = Field(gt=0)
    expected_revision: int = Field(ge=0)
    changes: CharacterOverrides


class CharacterCardExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_avatar_id: int = Field(gt=0)
    expected_revision: int = Field(ge=0)


class CompanionCharacterCard(ModelBase, TimestampMixin):
    __tablename__ = "companion_character_cards"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatar_assets.id", ondelete="CASCADE"), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    automatic_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    overrides_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    extraction_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"), index=True)
    portrait_status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    body_status: Mapped[str] = mapped_column(String(16), default="pending", server_default=text("'pending'"))
    portrait_result_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    body_result_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    portrait_source_path: Mapped[str] = mapped_column(String(2048))
    body_source_path: Mapped[str] = mapped_column(String(2048))
    portrait_source_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    body_source_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    portrait_pending_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    body_pending_hash: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
