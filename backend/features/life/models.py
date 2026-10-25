"""生活状态相关持久对象。

生活状态是当前生活的唯一权威；事件保存改变它的事实依据，业务任务仍由各自模块拥有。
"""

from datetime import datetime
from typing import Any

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

RESULT_REFERENCE_FIELDS = {
    "job_id": "result_job_id",
    "visual_id": "result_visual_id",
    "visual_version_id": "result_visual_version_id",
    "entry_id": "result_entry_id",
}


class LifeState(ModelBase, TimestampMixin):
    __tablename__ = "life_states"
    id = None
    __table_args__ = (CheckConstraint("revision >= 0 AND scene_revision >= 0", name="revisions_nonnegative"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    current_activity: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    current_scene_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    mood: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_pursuit_id: Mapped[int | None] = mapped_column(
        ForeignKey("life_pursuits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    scene_revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class LifePursuit(ModelBase, TimestampMixin):
    __tablename__ = "life_pursuits"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'completed')", name="pursuit_status"),
        Index("ix_life_pursuits_user_status", "user_id", "status", text("updated_at DESC"), text("id DESC")),
        Index("ix_life_pursuits_user_updated", "user_id", text("updated_at DESC"), text("id DESC")),
        UniqueConstraint("user_id", "idempotency_key", name="uq_life_pursuits_user_idempotency"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    title: Mapped[str] = mapped_column(String(160), default="", server_default=text("''"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    desired_result: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    progress: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    next_step: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default=text("'active'"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_ref_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))


class LifeStep(ModelBase, TimestampMixin):
    __tablename__ = "life_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["result_visual_id", "result_visual_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_life_step_result_version",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "state IN ('planned', 'running', 'completed', 'failed', 'cancelled', 'unknown')", name="step_state"
        ),
        Index("ix_life_steps_user_state", "user_id", "state"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_life_steps_user_idempotency"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pursuit_id: Mapped[int | None] = mapped_column(
        ForeignKey("life_pursuits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32), default="virtual", server_default=text("'virtual'"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    state: Mapped[str] = mapped_column(String(16), default="planned", server_default=text("'planned'"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    generation_id: Mapped[str | None] = mapped_column(String(64))
    result_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    result_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")
    )
    result_visual_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_versions.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")
    )
    result_entry_id: Mapped[int | None] = mapped_column(ForeignKey("companion_entries.id", ondelete="SET NULL"))
    result_ref_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LifeEvent(ModelBase, TimestampMixin):
    __tablename__ = "life_events"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(48))
    source: Mapped[str] = mapped_column(String(48))
    subject: Mapped[str] = mapped_column(String(24), default="companion", server_default=text("'companion'"))
    fact_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), index=True)
    pursuit_id: Mapped[int | None] = mapped_column(
        ForeignKey("life_pursuits.id", ondelete="SET NULL"), nullable=True, index=True
    )
    step_id: Mapped[int | None] = mapped_column(
        ForeignKey("life_steps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    result_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    result_visual_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_items.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")
    )
    result_visual_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("visual_versions.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED")
    )
    result_entry_id: Mapped[int | None] = mapped_column(ForeignKey("companion_entries.id", ondelete="SET NULL"))
    result_ref_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    is_meaningful: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["result_visual_id", "result_visual_version_id"],
            ["visual_versions.visual_id", "visual_versions.id"],
            name="fk_life_event_result_version",
            match="FULL",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("subject IN ('companion', 'user', 'system')", name="event_subject"),
        UniqueConstraint("user_id", "event_key", name="uq_life_events_user_key"),
        Index("ix_life_events_user_occurred", "user_id", text("occurred_at DESC"), text("id DESC")),
        Index("ix_life_events_pursuit_occurred", "user_id", "pursuit_id", text("occurred_at DESC"), text("id DESC")),
        Index("ix_life_events_user_kind", "user_id", "kind"),
    )


class PursuitEvidence(ModelBase):
    __tablename__ = "pursuit_evidence"
    __table_args__ = (UniqueConstraint("pursuit_id", "event_id", name="uq_pursuit_evidence_event"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pursuit_id: Mapped[int] = mapped_column(ForeignKey("life_pursuits.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("life_events.id", ondelete="CASCADE"), index=True)
