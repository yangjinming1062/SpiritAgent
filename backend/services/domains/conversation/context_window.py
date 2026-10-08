from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .formatting import format_messages_compact
from .main_conversation import UI_ONLY_SUBTYPES, get_special_conversation

RECENT_CONTEXT_CHAR_CAP = 200
CHECKPOINT_SUBTYPE = "compress_summary"


async def load_context_messages(db: AsyncSession, conv: Conversation) -> list[Message]:
    """按摘要的原消息覆盖边界读取未总结的历史，按消息 id 排序。"""
    checkpoint = await db.scalar(
        select(Message)
        .where(
            Message.conversation_id == conv.id,
            Message.id > conv.context_after_message_id,
            Message.subtype == CHECKPOINT_SUBTYPE,
        )
        .order_by(Message.id.desc())
        .limit(1),
    )
    stmt = select(Message).where(
        Message.conversation_id == conv.id,
        Message.id > conv.context_after_message_id,
        Message.subtype.is_(None) | Message.subtype.notin_((*UI_ONLY_SUBTYPES, CHECKPOINT_SUBTYPE)),
    )
    if checkpoint is not None:
        boundary = await db.scalar(
            select(Message).where(
                Message.conversation_id == conv.id,
                Message.id == checkpoint.summary_through_message_id,
                Message.subtype.is_(None) | (Message.subtype != CHECKPOINT_SUBTYPE),
            ),
        )
        if boundary is None:
            raise ValueError("Conversation summary requires an original message boundary")
        stmt = stmt.where(Message.id > boundary.id)
    rows = list((await db.scalars(stmt.order_by(Message.id))).all())
    return [checkpoint, *rows] if checkpoint is not None else rows


async def load_recent_context_window(db: AsyncSession, user_id: int, max_messages: int = 10) -> str:
    """主对话最近 N 条正常对话消息的紧凑文本，供空闲表达、自主行动决策和心情更新的一次性 prompt 使用。``status_proactive`` 保留（那是用户可以回应的真实轮次），只剔除 ``UI_ONLY_SUBTYPES``。"""
    main_conv = await get_special_conversation(db, user_id, "companion")
    if main_conv is None:
        return ""
    msgs = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == main_conv.id,
                    Message.id > main_conv.context_after_message_id,
                    Message.role.in_(("user", "assistant")),
                    # ``NULL NOT IN (...)`` 在 WHERE 中为 NULL（视为 false），显式 is_(None) 分支是保留普通消息的关键。
                    Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                    Message.tool_calls.is_(None),
                )
                .order_by(Message.id.desc())
                .limit(max_messages),
            )
        )
        .scalars()
        .all()
    )
    return format_messages_compact(list(reversed(msgs)), char_cap=RECENT_CONTEXT_CHAR_CAP)
