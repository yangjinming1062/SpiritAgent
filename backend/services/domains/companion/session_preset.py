from modules.conversation import Conversation
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import conversation_memory_scope

_WORK_PRESETS: frozenset[str] = frozenset({"developer", "product_manager", "copywriter", "language_teacher"})


def is_work_preset(preset_id: str | None) -> bool:
    return preset_id in _WORK_PRESETS


async def resolve_session_profile(db: AsyncSession, user_id: int, session_id: int | str | None) -> tuple[str, bool]:
    conv = await Conversation.by_session_id(db, str(session_id), user_id=user_id)
    if conv is None:
        raise ValueError("Conversation not found")
    conversation_memory_scope(conv, user_id)
    return conv.system_preset_id, conv.is_automation
