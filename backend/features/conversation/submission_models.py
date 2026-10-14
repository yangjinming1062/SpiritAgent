from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column


class PromptSubmission(ModelBase, TimestampMixin):
    __tablename__ = "prompt_submissions"
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uq_prompt_submissions_user_request"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="accepted", server_default=text("'accepted'"))
    error: Mapped[str | None] = mapped_column(Text)
    origin_kind: Mapped[str] = mapped_column(String(16))
    origin_id: Mapped[str | None] = mapped_column(String(64))
    message_ids_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
