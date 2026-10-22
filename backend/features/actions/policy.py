"""动作域策略：受理门禁、创建开关与模型可点播判定。"""

import asyncio

from infrastructure.config import SETTINGS

from features.actions.materials import AcceptedActionAsset
from features.actions.models import CompanionAction
from features.actions.schemas import ABSOLUTE_MAX_DURATION_SECONDS
from features.media.chain import MediaChainState

PLAY_INTENT_TTL_SECONDS = 30
# 制作完成前保存的表达意图有效期：过期只入库，不补播。
DEFERRED_PLAY_INTENT_TTL_SECONDS = 15 * 60

# 账户内动作制作命令串行；共享定义另由账户行锁与唯一约束去重。
_ACCEPT_LOCKS: dict[int, asyncio.Lock] = {}


def is_unsubmitted_video_plan(
    state: MediaChainState,
    *,
    provider_task_id: str | None = None,
    artifact_path: str | None = None,
    result_json: str | None = None,
    media_path: str | None = None,
) -> bool:
    """仅有冻结供应商计划，尚无任何视频尝试或产物。"""
    return (
        bool(state.providers)
        and state.phase == "ready"
        and state.next_index == 0
        and state.active_index is None
        and not state.stop_reason
        and not state.candidates
        and not state.queue_request_id
        and state.video_queue is None
        and not state.result_url
        and not state.source_path
        and state.call_seconds == 0
        and not provider_task_id
        and not artifact_path
        and not result_json
        and not media_path
    )


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


def is_expression_action(action: CompanionAction, material: AcceptedActionAsset | None) -> bool:
    """模型可点播的表达动作：已启用、素材就绪且不占系统产品槽位。"""
    return action.enabled and not action.system_slot and material is not None and material.media_type == "video"
