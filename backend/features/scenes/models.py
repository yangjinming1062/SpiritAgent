"""独立环境素材；素材状态由本表维护。"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from infrastructure.job_models import JobRecord
from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates


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
    __table_args__ = (UniqueConstraint("user_id", "source_key", name="uq_companion_scenes_user_source"),)

    source_key: Mapped[str] = mapped_column(String(36), default=lambda: uuid4().hex)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default=text("'pending'"), index=True)
    origin: Mapped[str] = mapped_column(String(16), default="user_request", server_default=text("'user_request'"))
    source: Mapped[str] = mapped_column(String(16), default="generated", server_default=text("'generated'"))
    target_size_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    source_size_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    image_size_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    title: Mapped[str] = mapped_column(String(80), default="", server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    requirements: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    prompt: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    media_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    upload_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    media_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    generation_origin_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    upload_source_path: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    reference_image: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    initial_job_id: Mapped[int | None] = mapped_column(ForeignKey("scene_jobs.id", ondelete="SET NULL", use_alter=True))
    replacement_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("scene_jobs.id", ondelete="SET NULL", use_alter=True)
    )
    initial_job: Mapped["SceneJob | None"] = relationship(
        foreign_keys=[initial_job_id], lazy="joined", post_update=True
    )
    replacement_job: Mapped["SceneJob | None"] = relationship(
        foreign_keys=[replacement_job_id], lazy="joined", post_update=True
    )

    def __init__(self, **values: Any) -> None:
        owner = values.pop("user_id")
        super().__init__(user_id=owner)
        self.initial_job = SceneJob(user_id=owner, scene=self, purpose="create", token=uuid4().hex)
        for key, value in values.items():
            setattr(self, key, value)

    @validates("status")
    def _availability_changed(self, key: str, value: str) -> str:
        if self.initial_job is None:
            raise ValueError("场景缺少初始制作任务")
        self.initial_job.availability = value
        return value


class SceneJob(JobRecord):
    """环境制作输入与采纳对象；新建和重做共用同一执行模型。"""

    __tablename__ = "scene_jobs"
    __table_args__ = (UniqueConstraint("owner_id", "token", name="uq_scene_jobs_owner_token"),)
    __mapper_args__ = {"polymorphic_identity": "scene.generate"}

    id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    scene_id: Mapped[int | None] = mapped_column(ForeignKey("companion_scenes.id", ondelete="SET NULL"), index=True)
    scene: Mapped[CompanionScene | None] = relationship(foreign_keys=[scene_id])
    purpose: Mapped[str] = mapped_column(String(16))
    token: Mapped[str] = mapped_column(String(36))
    availability: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    @validates("availability")
    def _availability_changed(self, key: str, value: str) -> str:
        self.state = {
            "pending": "running",
            "ready": "succeeded",
            "description_failed": "failed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[value]
        return value
