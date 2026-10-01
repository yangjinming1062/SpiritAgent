"""编辑与重试共用的目标定位：二者只作用于会话最后一条用户消息，过期请求不改历史。"""

from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .main_conversation import SPECIAL_KIND, STANDARD_KIND


async def find_last_user_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
) -> tuple[Conversation | None, Message | None]:
    """返回 (会话, 源消息)。会话不属于该用户或不允许改写历史时均为 None；``source_message_id`` 不再是最后一条已消费的普通用户消息、或已在上下文水位之下时消息为 None。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None or conv.kind not in (STANDARD_KIND, SPECIAL_KIND):
        return None, None
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
        return conv, None
    return conv, source
