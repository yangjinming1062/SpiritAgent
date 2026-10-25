"""持久任务的共享存储契约；具体业务通过 joined-table 详情拥有输入与产物。"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from infrastructure.models import ModelBase, TimestampMixin
from infrastructure.text import utc_now


class JobRecord(ModelBase, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_jobs_user_id"),
        UniqueConstraint("user_id", "source_key", name="uq_jobs_user_source"),
        CheckConstraint("revision >= 0", name="job_revision"),
        CheckConstraint(
            "state IN ('queued','running','paused','unknown','succeeded','failed','cancelled')", name="job_state"
        ),
        Index("ix_jobs_user_state_id", "user_id", "state", "id"),
        Index("ix_jobs_recovery", "kind", "state", "not_before"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str] = mapped_column(String(36), default=lambda: uuid4().hex)
    kind: Mapped[str] = mapped_column(String(48))
    state: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    stage: Mapped[str] = mapped_column(String(48), default="prepare", server_default="prepare")
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __mapper_args__ = {"polymorphic_on": kind, "polymorphic_identity": "generic", "eager_defaults": True}


@event.listens_for(JobRecord, "before_insert", propagate=True)
def initialize_job_timestamps(mapper: Mapper[JobRecord], connection: Connection, target: JobRecord) -> None:
    if "owner_id" in mapper.local_table.c:
        target.owner_id = target.user_id
    target.revision = target.revision or 0
    if target.state == "running" and target.started_at is None:
        target.started_at = utc_now()
    if target.state in {"succeeded", "failed", "cancelled"}:
        target.finished_at = target.finished_at or utc_now()


@event.listens_for(JobRecord, "before_update", propagate=True)
def advance_job_revision(mapper: Mapper[JobRecord], connection: Connection, target: JobRecord) -> None:
    state = inspect(target)
    if not any(state.attrs[prop.key].history.has_changes() for prop in mapper.column_attrs):
        return
    target.revision = (target.revision or 0) + 1
    if inspect(target).attrs.state.history.has_changes():
        if target.state == "running" and target.started_at is None:
            target.started_at = utc_now()
        target.finished_at = utc_now() if target.state in {"succeeded", "failed", "cancelled"} else None
