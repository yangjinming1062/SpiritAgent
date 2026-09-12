from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class Conversation(ModelBase, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("context_after_message_id >= 0", name="ck_conversations_context_watermark"),
        CheckConstraint("is_automation = (system_preset_id = 'automation')", name="ck_conversations_automation_preset"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # 值集合 {special, standard, im}：special = 系统预设对话（由 system_preset_id 区分具体预设），standard = 用户创建或任务型 Cron 使用的普通对话，im = 外部 IM 对话。
    kind: Mapped[str] = mapped_column(String(32), default="standard", server_default=text("'standard'"))
    system_preset_id: Mapped[str] = mapped_column(String(32), index=True)
    context_after_message_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    title: Mapped[str] = mapped_column(Text, default="New Conversation")
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 每会话 key-value 覆盖（reasoning/language）；会话挂载时从该列填入，会话删除时随 conversation 级联清除；跨 WS 重连存活（不像内存中的 RuntimeSession.settings）。
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 自动化任务会话仍属于 standard kind，但不参与陪伴夜间记忆、活跃度或主动触达判断。
    is_automation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), nullable=False)
    is_deletable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), nullable=False)
    is_renamable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="conversations")
    parent: Mapped["Conversation | None"] = relationship(remote_side="Conversation.id", back_populates="children")
    children: Mapped[list["Conversation"]] = relationship(back_populates="parent", passive_deletes=True)
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", passive_deletes=True)

    @classmethod
    async def by_session_id(
        cls,
        db: AsyncSession,
        session_id: str,
        user_id: int | None = None,
    ) -> "Conversation | None":
        """把 renderer 给的 session_id（Conversation.id 的 str 形式）解析为 Conversation；session_id 非数字、记录缺失、或传了 user_id 但记录不属于该用户时返 None，调用方自行决定如何提示。"""
        try:
            conv_id = int(session_id)
        except (ValueError, TypeError):
            return None
        stmt = select(cls).where(cls.id == conv_id)
        if user_id is not None:
            stmt = stmt.where(cls.user_id == user_id)
        return (await db.execute(stmt)).scalar_one_or_none()


class Message(ModelBase):
    __tablename__ = "messages"

    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(64))
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    turn_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # 标记 `content` 是纯文本（"text"）还是 JSON 编码的多模态 parts 数组（"multimodal_v1"）；取代旧 startswith("[") + 子串嗅探，后者会把合法用户输入误判。
    content_type: Mapped[str] = mapped_column(String(32), default="text", server_default=text("'text'"))
    # 助手消息附带的生成媒体（JSON 数组，元素为 {"type": "image"|"video"|"audio", "url": ..., "audio_url"?: ...}）；与 content 正交，读路径只送渲染端，不进 LLM 上下文。
    media_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 助手推理过程原文；只给工作台展示与历史水合，装配 Responses 输入时不回灌。
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    speech_style_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 在 subtype="daily_summary" 的 system 消息上设置，让每日 checkpoint 不用解析 content 文本就能读到截止日期；content 仍是人类可读版本，本列才是结构化源。
    summary_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    # 旧 fork 末条标记位的列存根——fork / undo 都已不再写入；列保留只为已存在数据不报错，待后续迁移移除。
    draft_anchor: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
