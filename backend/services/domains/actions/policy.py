"""动作域策略：受理门禁、制作额度与模型可点播判定。"""

import asyncio
from datetime import UTC, datetime, timedelta

from components import SETTINGS, utc_now
from modules.auth import lock_user_row
from modules.companion import (
    ABSOLUTE_MAX_DURATION_SECONDS,
    ActionBudgetStatus,
    ActionCreation,
    ActionProposal,
    CompanionAction,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .materials import accepted_action_asset

REJECTED_PROPOSAL_COOLDOWN_DAYS = 7
# 暂缓提案的期限：评审失败、额度不足多为暂时原因。重启自动重试自创建起算，超期不再随重启付费重审；展示给模型自最近一次暂缓（updated_at）起算，超期不再占用上下文。重提同一创意复用原行重新评审，复用后再次暂缓的不随重启重审，由模型核对条件后重提。
DEFERRED_PROPOSAL_WINDOW = timedelta(days=1)
PLAY_INTENT_TTL_SECONDS = 30
# 制作完成前保存的表达意图有效期：过期只入库，不补播。
DEFERRED_PLAY_INTENT_TTL_SECONDS = 15 * 60

# 用户级受理/评审串行锁：受理取锁前不访问数据库，排队等待不占连接；查重、门禁与 flush 锁内串行，调用方锁外提交，评审持锁至提交；并发同创意提案由 (user_id, source, idempotency_key) 唯一约束兜底。
_ACCEPT_LOCKS: dict[int, asyncio.Lock] = {}


class ActionPolicyError(RuntimeError):
    """策略拒绝；str 为公开文案。"""


def get_action_accept_lock(user_id: int) -> asyncio.Lock:
    return _ACCEPT_LOCKS.setdefault(user_id, asyncio.Lock())


def daily_create_limit(source: str) -> int:
    """制作（获批）额度上限；服务端强制，不向模型展示以免影响创建意图。"""
    if source == "user_requested":
        return SETTINGS.action_user_requested_create_daily_limit
    return SETTINGS.action_autonomous_create_daily_limit


async def _count_creations(db: AsyncSession, user_id: int, source: str, start: datetime) -> int:
    return (
        await db.scalar(
            select(func.count(ActionCreation.id)).where(
                ActionCreation.user_id == user_id,
                ActionCreation.source == source,
                ActionCreation.consumed_at > start,
            ),
        )
        or 0
    )


async def get_daily_budget_status(db: AsyncSession, user_id: int) -> ActionBudgetStatus:
    now = utc_now()
    start = now - timedelta(hours=24)
    return ActionBudgetStatus(
        budget_date=now.date().isoformat(),
        window_start=start.isoformat(),
        window_end=now.isoformat(),
        autonomous_create_used=await _count_creations(db, user_id, "autonomous", start),
        autonomous_create_limit=daily_create_limit("autonomous"),
        user_requested_create_used=await _count_creations(db, user_id, "user_requested", start),
        user_requested_create_limit=daily_create_limit("user_requested"),
    )


async def check_can_accept(
    db: AsyncSession,
    user_id: int,
    *,
    source: str,
    duration_seconds: float,
    pack_id: int,
    semantic_fingerprint: str,
) -> None:
    """受理门禁；不满足时抛 ActionPolicyError。"""
    if not 1 <= duration_seconds <= ABSOLUTE_MAX_DURATION_SECONDS:
        raise ActionPolicyError(f"动作时长须在 [1, {ABSOLUTE_MAX_DURATION_SECONDS:g}] 秒内")
    # 供应商只接受整秒时长；受理即拦，避免评审与姿态图费用打水漂。
    if abs(duration_seconds - round(duration_seconds)) > 1e-6:
        raise ActionPolicyError("动作时长需为整秒")

    # 同包同创意拒绝后 7 天抑制，从拒绝时刻起算；拒绝行不再改写（重提复用先回 pending），故用 updated_at 而非 created_at（复用行 created_at 可能远早于本次拒绝）。
    cutoff = datetime.now(UTC) - timedelta(days=REJECTED_PROPOSAL_COOLDOWN_DAYS)
    rejected = await db.scalar(
        select(func.count(ActionProposal.id)).where(
            ActionProposal.user_id == user_id,
            ActionProposal.pack_id == pack_id,
            ActionProposal.semantic_fingerprint == semantic_fingerprint,
            ActionProposal.status == "rejected",
            ActionProposal.updated_at >= cutoff,
        ),
    )
    if rejected:
        raise ActionPolicyError("同类动作近期已被拒绝，请换一个明确不同的创意")

    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        raise ActionPolicyError("自动创建新动作当前已关闭")


async def consume_create_slot(
    db: AsyncSession,
    user_id: int,
    *,
    source: str,
    creation_key: str,
    action_id: int | None = None,
) -> ActionCreation:
    """在制作受理事务中预留滚动24小时额度；同一制作键幂等，删包不返额。"""
    if source not in {"user_requested", "autonomous"}:
        raise ValueError("Unknown action creation source")
    await lock_user_row(db, user_id)
    existing = await db.scalar(
        select(ActionCreation).where(ActionCreation.user_id == user_id, ActionCreation.creation_key == creation_key),
    )
    if existing is not None:
        return existing
    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        raise ActionPolicyError("自动创建新动作当前已关闭")
    now = utc_now()
    if await _count_creations(db, user_id, source, now - timedelta(hours=24)) >= daily_create_limit(source):
        raise ActionPolicyError("最近24小时的动作制作额度已用完")
    entry = ActionCreation(
        user_id=user_id,
        source=source,
        creation_key=creation_key,
        action_id=action_id,
        consumed_at=now,
    )
    db.add(entry)
    await db.flush()
    return entry


def is_expression_action(action: CompanionAction) -> bool:
    """模型可点播的表达动作：已启用、素材就绪且不占系统产品槽位。"""
    material = accepted_action_asset(action)
    return action.enabled and not action.system_slot and material is not None and material.media_type == "video"
