"""失败回合复用用户行与工具结果，只允许补齐尚未保存的终端回复。"""

from modules.conversation import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .last_user_message import find_last_user_message


class ReplyRetryNotAllowedError(ValueError):
    """原请求已过期、已回复或不属于当前用户。"""


async def get_reply_retry_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
) -> Message:
    """调用方持有会话锁且确认没有在途回合；保留原消息及附件，不截断工具历史。"""
    conv, source = await find_last_user_message(db, user_id, session_id, source_message_id)
    if conv is None:
        raise ReplyRetryNotAllowedError("会话不存在或不支持重试回复")
    if source is None:
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
