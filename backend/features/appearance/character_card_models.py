"""已确认形象的固定特征及可恢复的双图分析任务。"""

from typing import Literal

from infrastructure.models import ModelBase, TimestampMixin
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from features.media.identity_contracts import CharacterCardSnapshot, CharacterFeatures, CharacterOverrides

ExtractionStatus = Literal["pending", "running", "ready", "failed"]


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
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
