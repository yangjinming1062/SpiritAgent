"""动态发布的专有输入、额度与产物；执行状态归共享任务行。"""

from datetime import date, datetime
from typing import Any

from infrastructure.job_models import JobRecord
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class PostPublication(JobRecord):
    __tablename__ = "post_publications"
    __mapper_args__ = {"polymorphic_identity": "post.publish"}

    id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    activity_date: Mapped[date] = mapped_column(Date)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    video_job_id: Mapped[int | None] = mapped_column(ForeignKey("video_gen_jobs.id", ondelete="SET NULL"), index=True)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("companion_entries.id", ondelete="SET NULL"), nullable=True)
