"""动作域策略：受理门禁、创建开关与模型可点播判定。"""

import asyncio

from infrastructure.config import SETTINGS

from features.actions.materials import accepted_action_asset
from features.actions.models import CompanionAction
from features.actions.schemas import ABSOLUTE_MAX_DURATION_SECONDS

PLAY_INTENT_TTL_SECONDS = 30
# 制作完成前保存的表达意图有效期：过期只入库，不补播。
DEFERRED_PLAY_INTENT_TTL_SECONDS = 15 * 60

# 账户内动作制作命令串行；共享定义另由账户行锁与唯一约束去重。
_ACCEPT_LOCKS: dict[int, asyncio.Lock] = {}


class ActionPolicyError(RuntimeError):
    """策略拒绝；str 为公开文案。"""


def get_action_accept_lock(user_id: int) -> asyncio.Lock:
    return _ACCEPT_LOCKS.setdefault(user_id, asyncio.Lock())


def check_creation_enabled(source: str) -> None:
    """新建与重做受理前复核来源及自主创建开关。"""
    if source not in {"user_requested", "autonomous"}:
        raise ValueError("Unknown action creation source")
    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        raise ActionPolicyError("自动创建新动作当前已关闭")


async def check_can_accept(
    *,
    source: str,
    duration_seconds: float,
) -> None:
    """受理门禁；不满足时抛 ActionPolicyError。"""
    if not 1 <= duration_seconds <= ABSOLUTE_MAX_DURATION_SECONDS:
        raise ActionPolicyError(f"动作时长须在 [1, {ABSOLUTE_MAX_DURATION_SECONDS:g}] 秒内")
    # 供应商只接受整秒时长；受理即拦，避免评审与姿态图费用打水漂。
    if abs(duration_seconds - round(duration_seconds)) > 1e-6:
        raise ActionPolicyError("动作时长需为整秒")

    check_creation_enabled(source)


def is_expression_action(action: CompanionAction) -> bool:
    """模型可点播的表达动作：已启用、素材就绪且不占系统产品槽位。"""
    material = accepted_action_asset(action)
    return action.enabled and not action.system_slot and material is not None and material.media_type == "video"
