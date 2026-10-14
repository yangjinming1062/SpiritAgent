from runtime.memory_scope import MemoryScope, validate_memory_scope
from sqlalchemy import ColumnElement, and_
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.models import Conversation
from features.conversation.snapshots import ConversationSnapshot


def user_authored_conversation() -> ColumnElement[bool]:
    """用户本人的对话：排除自动化会话，以及子 Agent 会话（其中的“用户”消息由父 Agent 撰写，不是用户发言）。"""
    return and_(Conversation.is_automation.is_(False), Conversation.parent_id.is_(None))


def conversation_memory_scope(conv: Conversation | ConversationSnapshot, user_id: int) -> MemoryScope | None:
    if conv.user_id != user_id:
        raise ValueError("Conversation not found")
    if conv.is_automation:
        return None
    scope = MemoryScope(user_id, conv.system_preset_id)
    validate_memory_scope(scope)
    return scope


async def resolve_memory_scope(db: AsyncSession, user_id: int, session_id: str) -> MemoryScope:
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None:
        raise ValueError("Conversation not found")
    scope = conversation_memory_scope(conv, user_id)
    if scope is None:
        raise ValueError("Memory is unavailable for automation")
    return scope
