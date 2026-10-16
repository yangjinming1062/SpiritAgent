from datetime import datetime

from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column


class CustomVoice(ModelBase, TimestampMixin):
    """不可变音色与临时样本；每种来源只有一项作为当前确认方案。"""

    __tablename__ = "custom_voices"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    model_card_id: Mapped[str] = mapped_column(String(36), default="")
    provider_voice_id: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    reference_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_path: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float] = mapped_column(Float, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    expected_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    expected_voice_id: Mapped[str] = mapped_column(Text, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        CheckConstraint("kind IN ('sample', 'design', 'clone')", name="voice_kind"),
        Index("uq_custom_voice_token", "user_id", "token", unique=True),
        Index("uq_current_custom_voice", "user_id", "kind", unique=True, postgresql_where=text("is_current")),
    )
