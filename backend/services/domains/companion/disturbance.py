from components import session_scope
from modules.settings import get_user_setting
from sqlalchemy.ext.asyncio import AsyncSession

# 打扰档位由客户端主导，生效值经配置同步管道落库到 user_settings 点键（PROTOCOL「配置所有权与云同步」），供重启后服务端门控继续生效。三档与 DESIGN「主动陪伴」对齐：still 硬切断主动外联，autonomous 才放行桌面视觉表达与空间决策。
ALLOWED_TIERS = frozenset({"autonomous", "normal", "still"})
DEFAULT_TIER = "normal"

TIER_SETTING_KEY = "companion.disturbance_tier"


async def get_disturbance_tier(user_id: int, *, db: AsyncSession | None = None) -> str:
    if db is None:
        async with session_scope() as session:
            return await get_disturbance_tier(user_id, db=session)
    value = await get_user_setting(db, user_id, TIER_SETTING_KEY)
    return value if isinstance(value, str) and value in ALLOWED_TIERS else DEFAULT_TIER


async def is_still(user_id: int) -> bool:
    return await get_disturbance_tier(user_id) == "still"
