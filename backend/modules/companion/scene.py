"""独立场景资产；当前环境由 Persona 的激活指针与切换版本维护。"""

from datetime import datetime
from enum import StrEnum

from common import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column


class SceneStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DESCRIPTION_FAILED = "description_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SceneOrigin(StrEnum):
    ONBOARDING = "onboarding"
    LLM = "llm"
    NIGHTLY = "nightly"
    USER_REQUEST = "user_request"


class ScenePolicy(StrEnum):
    LOCKED = "locked"
    LLM_MAY_REPLACE = "llm_may_replace"


class SceneSource(StrEnum):
    GENERATED = "generated"
    USER_UPLOAD = "user_upload"


class CompanionScene(ModelBase, TimestampMixin):
    __tablename__ = "companion_scenes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default=text("'pending'"), index=True)
    stage: Mapped[str] = mapped_column(String(24), default="prepare", server_default=text("'prepare'"))
    origin: Mapped[str] = mapped_column(String(16), default="user_request", server_default=text("'user_request'"))
    source: Mapped[str] = mapped_column(String(16), default="generated", server_default=text("'generated'"))
    character_card_json: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(80), default="", server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    requirements: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    outfit_description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    prompt: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    media_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    generation_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    secondary_reference_image: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    seed_portrait_media_id: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    identity_review: Mapped[str] = mapped_column(String(16), default="none", server_default=text("'none'"))
    identity_review_reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    auto_activate: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    switch_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SceneGenerationAttempt(ModelBase):
    """在线自主生图提交账本；不随场景删除或备份恢复重置付费记录。"""

    __tablename__ = "scene_generation_attempts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("companion_scenes.id", ondelete="SET NULL"), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
