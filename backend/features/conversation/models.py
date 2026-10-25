from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from infrastructure.api_ids import parse_resource_id
from infrastructure.constants import DEFAULT_SESSION_TITLE
from infrastructure.models import ModelBase, TimestampMixin
from sqlalchemy import (
    BigInteger,
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
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from features.accounts.models import User


def parse_session_id(session_id: str) -> int | None:
    try:
        return parse_resource_id(session_id)
    except (ValueError, TypeError):
        return None


class Conversation(ModelBase, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_conversations_user_id"),
        CheckConstraint("context_after_message_id >= 0", name="ck_conversations_context_watermark"),
        CheckConstraint(
            "visible_message_count >= 0 AND input_token_count >= 0 AND output_token_count >= 0 AND tool_call_count >= 0",
            name="ck_conversations_statistics_nonnegative",
        ),
        Index("ix_conversations_user_message_count", "user_id", text("visible_message_count DESC"), "id"),
        CheckConstraint("is_automation = (system_preset_id = 'automation')", name="ck_conversations_automation_preset"),
        CheckConstraint("kind != 'special' OR system_preset_id = 'companion'", name="ck_conversations_main_preset"),
        Index(
            "uq_conversations_user_preset",
            "user_id",
            "system_preset_id",
            unique=True,
            postgresql_where=text("kind = 'special' AND system_preset_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 仅子 Agent 会话指向发起会话，随其级联删除。
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # 派生来源；来源删除时置空，派生会话保留。
    forked_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # special=唯一陪伴主会话；standard=用户创建或任务型 Cron。
    kind: Mapped[str] = mapped_column(String(32), default="standard", server_default=text("'standard'"))
    system_preset_id: Mapped[str] = mapped_column(String(32), index=True)
    memory_reviewed_message_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    context_after_message_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    history_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 派生计数由 messages 语句级触发器维护，不经 ORM 写入或备份。
    visible_message_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    input_token_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_token_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    tool_call_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    title: Mapped[str] = mapped_column(Text, default=DEFAULT_SESSION_TITLE)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # reasoning/language 的 key-value 覆盖；随 conversation 级联清除，跨 WS 重连存活（不像 RuntimeSession.settings）。
    settings_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 自动化任务会话仍属 standard kind，但不参与陪伴夜间记忆、活跃度或主动触达判断。
    is_automation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), nullable=False)
    is_deletable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), nullable=False)
    is_renamable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="conversations")
    parent: Mapped["Conversation | None"] = relationship(
        remote_side="Conversation.id",
        foreign_keys="Conversation.parent_id",
        back_populates="children",
    )
    children: Mapped[list["Conversation"]] = relationship(
        foreign_keys="Conversation.parent_id",
        back_populates="parent",
        passive_deletes=True,
    )
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", passive_deletes=True)

    @classmethod
    async def by_session_id(
        cls,
        db: AsyncSession,
        session_id: str,
        user_id: int,
    ) -> "Conversation | None":
        """session_id 为 Conversation.id 的 str 形式；非法、缺失或不属于该 user_id 时返 None。"""
        conv_id = parse_session_id(session_id)
        if conv_id is None:
            return None
        stmt = select(cls).where(cls.id == conv_id, cls.user_id == user_id)
        return (await db.execute(stmt)).scalar_one_or_none()


class Message(ModelBase):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_messages_user_id"),
        ForeignKeyConstraint(
            ["user_id", "conversation_id"],
            ["conversations.user_id", "conversations.id"],
            name="fk_messages_owned_conversation",
            ondelete="CASCADE",
        ),
        Index("ix_messages_conversation_order", "conversation_id", "id"),
        Index(
            "ix_messages_conversation_user_preview",
            "conversation_id",
            "id",
            postgresql_where=text("role = 'user' AND content IS NOT NULL AND content != ''"),
        ),
        CheckConstraint(
            "content_type IN ('text', 'multimodal_v1', 'companion_reply')",
            name="ck_messages_content_type",
        ),
        CheckConstraint(
            "(content_type = 'companion_reply') = (reply_json IS NOT NULL)",
            name="ck_messages_reply_delivery",
        ),
        CheckConstraint(
            "content_type != 'companion_reply' OR (role = 'assistant' AND content IS NOT NULL AND tool_calls IS NULL)",
            name="ck_messages_reply_content",
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(64))
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    turn_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    content_type: Mapped[Literal["text", "multimodal_v1", "companion_reply"]] = mapped_column(
        String(32),
        default="text",
        server_default=text("'text'"),
    )
    # 文本会话的生成附件；结构化回复的媒体保存在 reply_json。
    media_json: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    # 助手推理过程原文；只给工作台展示与历史水合，装配 Responses 输入时不回灌。
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_origin_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # content=模型气泡数组；reply_json=语音绑定、音频及媒体交付态。
    reply_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 摘要实际覆盖到的原消息 id，与摘要插入位置分开。
    summary_through_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class MessageAsset(ModelBase):
    __tablename__ = "message_assets"
    __table_args__ = (UniqueConstraint("message_id", "asset_id", name="uq_message_asset"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)


class MessageVoiceRef(ModelBase):
    """已保存消息使用的本地声音版本；外部供应商声音标识不建立本地关系。"""

    __tablename__ = "message_voice_refs"
    __table_args__ = (UniqueConstraint("message_id", "voice_id", name="uq_message_voice_ref"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    voice_id: Mapped[int] = mapped_column(
        ForeignKey("custom_voices.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"),
        index=True,
    )
