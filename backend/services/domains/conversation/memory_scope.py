from modules.conversation import Conversation
from sqlalchemy import ColumnElement, and_
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope

from .presets import SYSTEM_PRESET_CATALOG


def validate_memory_scope(scope: MemoryScope) -> None:
    if scope.user_id <= 0 or scope.system_preset_id not in SYSTEM_PRESET_CATALOG:
        raise ValueError("Invalid memory scope")


def user_authored_conversation() -> ColumnElement[bool]:
    """用户本人的对话：排除自动化会话，以及子 Agent 会话（其中的“用户”消息由父 Agent 撰写，不是用户发言）。"""
    return and_(Conversation.is_automation.is_(False), Conversation.parent_id.is_(None))


def conversation_memory_scope(conv: Conversation, user_id: int) -> MemoryScope | None:
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
