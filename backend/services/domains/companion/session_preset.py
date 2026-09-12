"""会话类型判定：工作预设集合与供工具装配层读取的最小会话档案（预设 + 自动化标记）。"""

from modules.conversation import Conversation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_WORK_PRESETS: frozenset[str] = frozenset({"developer", "product_manager", "copywriter", "language_teacher", "pm"})


def is_life_preset(preset_id: str | None) -> bool:
    return preset_id not in _WORK_PRESETS


def is_work_preset(preset_id: str | None) -> bool:
    return preset_id in _WORK_PRESETS


async def resolve_session_profile(db: AsyncSession, session_id: int | str | None) -> tuple[str | None, bool]:
    """返回会话的 ``(system_preset_id, is_automation)``；未知 / 非法 session 一律回落 (None, False)。"""
    if session_id is None:
        return None, False
    try:
        sid_int = int(session_id)
    except (TypeError, ValueError):
        return None, False
    row = (
        await db.execute(
            select(Conversation.system_preset_id, Conversation.is_automation).where(Conversation.id == sid_int),
        )
    ).one_or_none()
    return (row.system_preset_id, row.is_automation) if row is not None else (None, False)
