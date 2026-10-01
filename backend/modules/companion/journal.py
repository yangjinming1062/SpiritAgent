"""伙伴每日日记。"""

from datetime import date
from enum import StrEnum
from uuid import uuid4

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    ARRAY,
    Date,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class DiarySource(StrEnum):
    NIGHTLY = "nightly"
    LLM = "llm"


class CompanionDiaryEntry(ModelBase, TimestampMixin):
    """每日一篇第一人称日记；唯一约束 (user_id, entry_date) 决定夜间任务走 upsert。"""

    __tablename__ = "companion_diary_entries"
    __table_args__ = (UniqueConstraint("user_id", "entry_date", name="uq_companion_diary_user_date"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(
        String(128),
        default="",
        server_default=text("''"),
    )
    body: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    mood: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(
        String(16),
        default=DiarySource.NIGHTLY.value,
        server_default=text("'nightly'"),
    )
    post_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'"),
    )
