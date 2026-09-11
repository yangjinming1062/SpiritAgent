"""会话系统预设判定：判断当前回合是否落在工作预设会话（不应写生活空间时刻/日记/房间图）。"""

from modules.conversation import Conversation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_WORK_PRESETS: frozenset[str] = frozenset({"developer", "product_manager", "copywriter", "language_teacher", "pm"})


def is_life_preset(preset_id: str | None) -> bool:
    return preset_id not in _WORK_PRESETS


def is_work_preset(preset_id: str | None) -> bool:
    return preset_id in _WORK_PRESETS


async def resolve_session_preset(db: AsyncSession, session_id: int | str | None) -> str | None:
    if session_id is None:
        return None
    try:
        sid_int = int(session_id)
    except (TypeError, ValueError):
        return None
    return (
        await db.execute(select(Conversation.system_preset_id).where(Conversation.id == sid_int))
    ).scalar_one_or_none()
