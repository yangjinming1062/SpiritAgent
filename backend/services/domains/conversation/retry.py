"""失败回合复用用户行与工具结果，只允许补齐尚未保存的终端回复。"""

from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .main_conversation import SPECIAL_KIND, STANDARD_KIND


class ReplyRetryNotAllowedError(ValueError):
    """原请求已过期、已回复或不属于当前用户。"""


async def get_reply_retry_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
) -> Message:
    """调用方持有会话锁且确认没有在途回合；保留原消息及附件，不截断工具历史。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None or conv.kind not in (STANDARD_KIND, SPECIAL_KIND):
        raise ReplyRetryNotAllowedError("会话不存在或不支持重试回复")
    source = await db.scalar(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.role == "user")
        .order_by(Message.id.desc())
        .limit(1),
    )
    if (
        source is None
        or source.id != source_message_id
        or source.subtype
        or source.queued
        or source.id <= conv.context_after_message_id
    ):
        raise ReplyRetryNotAllowedError("只能重试当前对话最后一条未回复的消息，请刷新后重试")
    completed = await db.scalar(
        select(Message.id)
        .where(
            Message.conversation_id == conv.id,
            Message.id > source.id,
            Message.role == "assistant",
            Message.subtype.is_(None),
            Message.tool_calls.is_(None),
        )
        .limit(1),
    )
    if completed is not None:
        raise ReplyRetryNotAllowedError("回复已保存，请刷新对话查看")
    return source
