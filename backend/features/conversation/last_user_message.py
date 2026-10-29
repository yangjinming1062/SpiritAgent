"""会话用户消息目标定位；编辑可定位任意有效用户行，重试仍只定位最后一行。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.main_conversation import SPECIAL_KIND, STANDARD_KIND
from features.conversation.models import Conversation, Message


async def _find_editable_conversation(db: AsyncSession, user_id: int, session_id: str) -> Conversation | None:
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)

    if conv is None or conv.is_automation or conv.kind not in (STANDARD_KIND, SPECIAL_KIND):
        return None

    return conv


def _is_editable_user_message(source: Message | None, conv: Conversation) -> bool:
    return source is not None and source.subtype is None and source.id > conv.context_after_message_id


async def find_user_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
) -> tuple[Conversation | None, Message | None]:
    """返回会话中指定的可编辑普通用户行；过期请求不改历史。"""
    conv = await _find_editable_conversation(db, user_id, session_id)

    if conv is None:
        return None, None

    source = await db.scalar(
        select(Message).where(
            Message.conversation_id == conv.id,
            Message.id == source_message_id,
            Message.role == "user",
        ),
    )

    return conv, source if _is_editable_user_message(source, conv) else None


async def find_last_user_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
) -> tuple[Conversation | None, Message | None]:
    """返回会话最后一条可消费的普通用户行；重试过期请求不改历史。"""
    conv = await _find_editable_conversation(db, user_id, session_id)

    if conv is None:
        return None, None

    source = await db.scalar(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.role == "user")
        .order_by(Message.id.desc())
        .limit(1),
    )

    if source is None or source.id != source_message_id or not _is_editable_user_message(source, conv):
        return conv, None

    return conv, source
