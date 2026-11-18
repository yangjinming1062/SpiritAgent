"""失败回合复用用户行与工具结果，只允许补齐尚未保存的终端回复。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.last_user_message import find_last_user_message
from features.conversation.models import Message
from features.conversation.submission_models import PromptSubmission, SubmissionMessage


class ReplyRetryNotAllowedError(ValueError):
    """原请求已过期、已回复或不属于当前用户。"""


async def get_reply_retry_message(db: AsyncSession, user_id: int, session_id: str, source_message_id: int) -> Message:
    """只读核对原输入及终态。受理重试时在会话锁内复核，保留原消息、附件与工具历史。"""
    conv, source = await find_last_user_message(db, user_id, session_id, source_message_id)
    if conv is None:
        raise ReplyRetryNotAllowedError("会话不存在或不支持重试回复")
    if source is None:
        raise ReplyRetryNotAllowedError("只能重试当前对话最后一条未回复的消息，请刷新后重试")
    latest_status = (
        select(PromptSubmission.status)
        .join(SubmissionMessage, SubmissionMessage.submission_id == PromptSubmission.id)
        .where(
            SubmissionMessage.message_id == source.id,
            PromptSubmission.user_id == user_id,
            PromptSubmission.conversation_id == conv.id,
        )
        .order_by(PromptSubmission.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    saved_reply = select(Message.id).where(
        Message.conversation_id == conv.id,
        Message.id > source.id,
        Message.role == "assistant",
        Message.subtype.is_(None),
        Message.tool_calls.is_(None),
    )
    if await db.scalar(select(or_(saved_reply.exists(), latest_status.in_(("silent", "deferred"))))):
        raise ReplyRetryNotAllowedError("这一回合已结束，请刷新对话查看")
    return source
