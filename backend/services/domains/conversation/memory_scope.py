from modules.conversation import Conversation
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts.memory import MemoryScope

from .presets import SYSTEM_PRESET_CATALOG


def validate_memory_scope(scope: MemoryScope) -> None:
    if scope.user_id <= 0 or scope.system_preset_id not in SYSTEM_PRESET_CATALOG:
        raise ValueError("Invalid memory scope")


def conversation_memory_scope(conv: Conversation, user_id: int) -> MemoryScope | None:
    if conv.user_id != user_id:
        raise ValueError("Conversation not found")
    if conv.is_automation:
        if conv.system_preset_id != "automation":
            raise ValueError("Automation conversation has an invalid preset")
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
