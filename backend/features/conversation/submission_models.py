from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Mapped, column_property, mapped_column


class SubmissionMessage(ModelBase):
    __tablename__ = "submission_messages"
    __table_args__ = (
        UniqueConstraint("submission_id", "message_id", name="uq_submission_message"),
        UniqueConstraint("submission_id", "position", name="uq_submission_message_position"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("prompt_submissions.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)


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
    replace_reply_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    result_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)


PromptSubmission.message_ids = column_property(
    select(
        func.coalesce(
            func.array_agg(aggregate_order_by(SubmissionMessage.message_id, SubmissionMessage.position)),
            text("'{}'::bigint[]"),
        )
    )
    .where(SubmissionMessage.submission_id == PromptSubmission.id)
    .correlate_except(SubmissionMessage)
    .scalar_subquery()
)
