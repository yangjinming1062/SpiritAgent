from modules.conversation import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .formatting import format_messages_compact
from .main_conversation import UI_ONLY_SUBTYPES, get_special_conversation

RECENT_CONTEXT_CHAR_CAP = 200


async def load_recent_context_window(db: AsyncSession, user_id: int, max_messages: int = 10) -> str:
    """主对话最近 N 条正常对话消息的紧凑文本，供戳与 idle affect 的一次性 prompt 使用。

    ``status_proactive`` 保留（那是用户可以回应的真实轮次），只剔除 ``UI_ONLY_SUBTYPES``。
    """
    main_conv = await get_special_conversation(db, user_id, "companion")
    if main_conv is None:
        return ""
    msgs = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == main_conv.id,
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
    msgs.reverse()
    return format_messages_compact(msgs, char_cap=RECENT_CONTEXT_CHAR_CAP)
