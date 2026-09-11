"""伙伴日记产品化资源：moments（生活空间时间线）+ diary_entries（每日第一人称日记）。

``memories`` 仍服务检索与注入对话上下文，不替代。moments / diary 是给用户看的展示面。
"""

from datetime import date, datetime
from enum import StrEnum
from uuid import uuid4

from common import ModelBase, TimestampMixin
from sqlalchemy import (
    ARRAY,
    JSON,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class MomentKind(StrEnum):
    GREETING = "greeting"
    EMOTION = "emotion"
    TOGETHER = "together"
    MILESTONE = "milestone"
    SCENE = "scene"
    USER = "user"


class MomentSource(StrEnum):
    SYSTEM = "system"
    NIGHTLY = "nightly"
    LLM = "llm"
    USER = "user"


class MomentVisibility(StrEnum):
    SHOWN = "shown"
    HIDDEN = "hidden"


class DiarySource(StrEnum):
    NIGHTLY = "nightly"
    LLM = "llm"
    USER = "user"


class CompanionMoment(ModelBase, TimestampMixin):
    """生活空间时间线单条——情绪切片，不是检索向量。"""

    __tablename__ = "companion_moments"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(16),
        default=MomentKind.GREETING.value,
        server_default=text("'greeting'"),
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(64),
        default="",
        server_default=text("''"),
    )
    body: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str] = mapped_column(
        String(16),
        default="",
        server_default=text("''"),
    )
    audio_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(
        String(16),
        default=MomentSource.SYSTEM.value,
        server_default=text("'system'"),
    )
    memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("memories.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=MomentVisibility.SHOWN.value,
        server_default=text("'shown'"),
    )


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
    memory_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'"),
    )
    moment_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'"),
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
