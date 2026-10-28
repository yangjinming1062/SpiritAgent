"""动态表现设置：控制客户端视觉活动强度，与主动联系档位相互独立。"""

from infrastructure.database import session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.values import get_user_setting

# 云同步点键（PROTOCOL「配置所有权与云同步」）：off 仅静态画面，subtle 只保留安静循环，
# standard 启用完整常驻动态与互动演出。安静陪伴档位不改变本设置，临时安静也不停止静音待机。
ALLOWED_LEVELS = frozenset({"off", "subtle", "standard"})
DEFAULT_LEVEL = "standard"

MOTION_SETTING_KEY = "companion.motion_level"


async def get_motion_level(user_id: int, *, db: AsyncSession | None = None) -> str:
    if db is None:
        async with session_scope() as session:
            return await get_motion_level(user_id, db=session)
    value = await get_user_setting(db, user_id, MOTION_SETTING_KEY)
    return value if isinstance(value, str) and value in ALLOWED_LEVELS else DEFAULT_LEVEL
