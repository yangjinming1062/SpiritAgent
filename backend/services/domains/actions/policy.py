"""动作域策略：权限、配额、抑制与硬门禁。

模型负责提案，本模块做服务端确定性校验。制作额度与近 7 天拒绝抑制从
ActionProposal 聚合；评审不设日限额，仅 approve 后占用制作额度。
"""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import SETTINGS
from modules.companion import ActionBudgetStatus, ActionProposal, CompanionAction
from modules.settings import get_user_setting
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

REJECTED_PROPOSAL_COOLDOWN_DAYS = 7
PLAY_INTENT_TTL_SECONDS = 30
# 制作完成前保存的表达意图有效期：过期只入库，不补播。
DEFERRED_PLAY_INTENT_TTL_SECONDS = 15 * 60

# 用户级受理/评审串行锁：受理到提案落库在同一进程内原子化，跨进程由
# (user_id, source, idempotency_key) 唯一约束兜底。
_ACCEPT_LOCKS: dict[int, asyncio.Lock] = {}


class ActionPolicyError(RuntimeError):
    """策略拒绝；str 为公开文案。"""


def get_action_accept_lock(user_id: int) -> asyncio.Lock:
    return _ACCEPT_LOCKS.setdefault(user_id, asyncio.Lock())


def max_duration_seconds() -> float:
    return float(SETTINGS.action_max_duration_seconds)


def daily_create_limit(source: str) -> int:
    """制作（获批）额度上限；服务端强制，不向模型展示以免影响创建意图。"""
    if source == "user_requested":
        return SETTINGS.action_user_requested_create_daily_limit
    return SETTINGS.action_autonomous_create_daily_limit


async def resolve_action_budget_zone(db: AsyncSession, user_id: int) -> ZoneInfo | None:
    """用户本地时区（IANA，桌面握手上报）；缺失或非法时回落 None（按 UTC 日切）。"""
    tz = await get_user_setting(db, user_id, "timezone")
    try:
        return ZoneInfo(tz) if tz else None
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
        return None


async def _budget_day(db: AsyncSession, user_id: int) -> tuple[date, datetime, datetime]:
    """额度结算日按用户本地时区（缺失时 UTC）；返回本地日及其 UTC 起止，与 approved_at 比较。"""
    zone = await resolve_action_budget_zone(db, user_id) or UTC
    today = datetime.now(zone).date()
    start = datetime.combine(today, time.min, tzinfo=zone)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone)
    return today, start.astimezone(UTC), end.astimezone(UTC)


async def _count_approved(db: AsyncSession, user_id: int, source: str, start: datetime, end: datetime) -> int:
    """统计窗口内已获批（制作）量：按评审 approve 时刻（approved_at）计。"""
    stmt = select(func.count(ActionProposal.id)).where(
        ActionProposal.user_id == user_id,
        ActionProposal.source == source,
        ActionProposal.approved_at >= start,
        ActionProposal.approved_at < end,
        ActionProposal.review_decision == "approve",
    )
    return (await db.execute(stmt)).scalar_one() or 0


async def get_daily_budget_status(
    db: AsyncSession,
    user_id: int,
) -> ActionBudgetStatus:
    today, start, end = await _budget_day(db, user_id)
    return ActionBudgetStatus(
        budget_date=today.isoformat(),
        autonomous_create_used=await _count_approved(db, user_id, "autonomous", start, end),
        autonomous_create_limit=daily_create_limit("autonomous"),
        user_requested_create_used=await _count_approved(db, user_id, "user_requested", start, end),
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
    """受理门禁；不满足时抛 ActionPolicyError。评审不设日限额。"""
    if duration_seconds > max_duration_seconds():
        raise ActionPolicyError(f"单动作最长 {max_duration_seconds():g} 秒")
    # 供应商只接受整秒时长；受理即拦，避免评审与姿态图费用打水漂。
    if abs(duration_seconds - round(duration_seconds)) > 1e-6:
        raise ActionPolicyError("动作时长需为整秒")

    # 同包近 7 天被拒绝的同一创意受抑制。
    cutoff = datetime.now(UTC) - timedelta(days=REJECTED_PROPOSAL_COOLDOWN_DAYS)
    rejected = await db.scalar(
        select(func.count(ActionProposal.id)).where(
            ActionProposal.user_id == user_id,
            ActionProposal.pack_id == pack_id,
            ActionProposal.semantic_fingerprint == semantic_fingerprint,
            ActionProposal.status == "rejected",
            ActionProposal.created_at >= cutoff,
        ),
    )
    if rejected:
        raise ActionPolicyError("同类动作近期已被拒绝，请换一个明确不同的创意")

    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        raise ActionPolicyError("自动创建新动作当前已关闭")


async def consume_create_slot(db: AsyncSession, user_id: int, *, source: str) -> None:
    """approve 后校验制作额度；超限抛 ActionPolicyError，调用方回退 defer。"""
    _, start, end = await _budget_day(db, user_id)
    if await _count_approved(db, user_id, source, start, end) >= daily_create_limit(source):
        raise ActionPolicyError("今日动作制作额度已用完")


def is_expression_action(action: CompanionAction) -> bool:
    """模型可点播的表达动作：已启用、素材就绪且不占系统产品槽位。"""
    return action.enabled and action.status == "succeeded" and bool(action.video_path) and not action.system_slot
