from components import session_scope
from modules.settings import UserSetting
from sqlalchemy import select

# 打扰档位由客户端主导（本地偏好 + 活动监视器覆盖），生效值经配置同步管道落库到
# user_settings 点键（PROTOCOL §2.4），供重启后服务端门控继续生效。
# 三档与 DESIGN §6.2 对齐：静止（still）在服务端硬切断主动外联；桌面视觉表达与空间决策
# 仅在自主（autonomous）档放行。客户端按同一生效档位控制展示、语音与精灵动作。
ALLOWED_TIERS = frozenset({"autonomous", "normal", "still"})
DEFAULT_TIER = "normal"

TIER_SETTING_KEY = "companion.disturbance_tier"


def _normalize(tier: str) -> str:
    return tier if tier in ALLOWED_TIERS else DEFAULT_TIER


async def get_disturbance_tier(user_id: int) -> str:
    async with session_scope() as db:
        value = (
            await db.execute(
                select(UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key == TIER_SETTING_KEY,
                ),
            )
        ).scalar_one_or_none()
    return _normalize(value) if isinstance(value, str) else DEFAULT_TIER


async def is_still(user_id: int) -> bool:
    return await get_disturbance_tier(user_id) == "still"
