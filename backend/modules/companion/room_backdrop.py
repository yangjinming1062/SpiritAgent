"""伙伴房间图：生活空间背景——角色必须入画。

每行是一次生图尝试；同一 persona 同时只允许一个 pending，新请求把旧 pending 标 superseded。
``Persona.active_backdrop_id`` 指向当前展示的 ready 行（与 brief.outfit_fingerprint 对照判断穿帮）。
"""

from datetime import datetime
from enum import StrEnum

from common import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column


class BackdropStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class BackdropOrigin(StrEnum):
    ONBOARDING = "onboarding"
    OUTFIT = "outfit"
    LLM = "llm"
    NIGHTLY = "nightly"
    USER_REQUEST = "user_request"
    ROLLBACK = "rollback"


class BackdropIntent(StrEnum):
    DECORATE = "decorate"
    SEASONAL = "seasonal"
    MOOD = "mood"
    REBUILD = "rebuild"


class BackdropPolicy(StrEnum):
    LOCKED = "locked"
    LLM_MAY_REPLACE = "llm_may_replace"


class CompanionRoomBackdrop(ModelBase, TimestampMixin):
    """伙伴的房间图行；status 流转 pending → ready | failed；superseded 表示被更新取代。"""

    __tablename__ = "companion_room_backdrops"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(
        String(16),
        default=BackdropStatus.PENDING.value,
        server_default=text("'pending'"),
        index=True,
    )
    origin: Mapped[str] = mapped_column(
        String(16),
        default=BackdropOrigin.ONBOARDING.value,
        server_default=text("'onboarding'"),
    )
    intent: Mapped[str] = mapped_column(
        String(16),
        default=BackdropIntent.DECORATE.value,
        server_default=text("'decorate'"),
    )
    brief: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    prompt: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    media_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    public_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_portrait_media_id: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_outfit_media_id: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    outfit_fingerprint: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"), index=True)
    contains_character: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    error_utterance: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
