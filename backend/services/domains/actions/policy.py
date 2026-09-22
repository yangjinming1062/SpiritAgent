"""动作域策略：权限、配额、抑制与硬门禁。

模型负责提案，本模块做服务端确定性校验。制作额度与近 7 天拒绝抑制从
ActionProposal 聚合；评审不设日限额，仅 approve 后占用制作额度。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import SETTINGS
from modules.companion.actions import (
    ActionProposal,
    CompanionAction,
)
from modules.companion.schemas_actions import ActionBudgetStatus
from modules.settings import UserSetting
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
    tz = (
        await db.execute(
            select(UserSetting.setting_value).where(
                UserSetting.user_id == user_id,
                UserSetting.setting_key == "timezone",
            ),
        )
    ).scalar()
    tz = (tz or "").strip()
    try:
        return ZoneInfo(tz) if tz else None
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


async def resolve_action_budget_date(db: AsyncSession, user_id: int) -> str:
    """额度结算日按用户本地时区；缺失时回落 UTC。"""
    zone = await resolve_action_budget_zone(db, user_id)
    now = datetime.now(UTC)
    if zone is not None:
        now = now.astimezone(zone)
    return now.strftime("%Y-%m-%d")


def _budget_window(budget_date: str, zone: ZoneInfo | None) -> tuple[datetime, datetime]:
    """本地日窗口：在用户时区解析当日/次日午夜再换算 UTC，与 created_at/approved_at 比较。

    不能把本地日字符串当成 UTC 零点，否则本地凌晨会把「今天」的提案算进昨天窗口。
    """
    day = datetime.strptime(budget_date, "%Y-%m-%d")  # noqa: DTZ007 — 本地日字符串，仅作日历日
    next_day = day + timedelta(days=1)
    if zone is not None:
        start = day.replace(tzinfo=zone)
        end = next_day.replace(tzinfo=zone)
    else:
        start = day.replace(tzinfo=UTC)
        end = next_day.replace(tzinfo=UTC)
    return start.astimezone(UTC), end.astimezone(UTC)


async def count_create_used(
    db: AsyncSession,
    user_id: int,
    budget_date: str,
    *,
    source: str | None = None,
) -> int:
    """统计当日已获批（制作）量：按评审 approve 时刻（approved_at）计。"""
    zone = await resolve_action_budget_zone(db, user_id)
    start, end = _budget_window(budget_date, zone)
    stmt = select(func.count(ActionProposal.id)).where(
        ActionProposal.user_id == user_id,
        ActionProposal.approved_at >= start,
        ActionProposal.approved_at < end,
        ActionProposal.review_decision == "approve",
    )
    if source is not None:
        stmt = stmt.where(ActionProposal.source == source)
    return (await db.execute(stmt)).scalar_one() or 0


async def get_daily_budget_status(
    db: AsyncSession,
    user_id: int,
) -> ActionBudgetStatus:
    budget_date = await resolve_action_budget_date(db, user_id)
    auto_create = await count_create_used(db, user_id, budget_date, source="autonomous")
    user_create = await count_create_used(db, user_id, budget_date, source="user_requested")
    return ActionBudgetStatus(
        budget_date=budget_date,
        autonomous_create_used=auto_create,
        autonomous_create_limit=daily_create_limit("autonomous"),
        user_requested_create_used=user_create,
        user_requested_create_limit=daily_create_limit("user_requested"),
    )


async def check_can_accept(
    db: AsyncSession,
    user_id: int,
    *,
    source: str,
    duration_seconds: float,
    pack_id: int | None = None,
    semantic_fingerprint: str = "",
) -> None:
    """受理门禁；不满足时抛 ActionPolicyError。评审不设日限额。"""
    if duration_seconds > max_duration_seconds():
        raise ActionPolicyError(f"单动作最长 {max_duration_seconds():g} 秒")
    # 供应商只接受整秒时长；受理即拦，避免评审与姿态图费用打水漂。
    if abs(duration_seconds - round(duration_seconds)) > 1e-6:
        raise ActionPolicyError("动作时长需为整秒")

    if semantic_fingerprint and await check_suppression(
        db,
        user_id,
        pack_id=pack_id,
        semantic_fingerprint=semantic_fingerprint,
    ):
        raise ActionPolicyError("同类动作近期已被拒绝，请换一个明确不同的创意")

    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        raise ActionPolicyError("自动创建新动作当前已关闭")


async def consume_create_slot(
    db: AsyncSession,
    user_id: int,
    *,
    source: str,
    budget_date: str | None = None,
) -> None:
    """approve 后校验制作额度；超限抛 ActionPolicyError，调用方回退 defer。"""
    budget_date = budget_date or await resolve_action_budget_date(db, user_id)
    used = await count_create_used(db, user_id, budget_date, source=source)
    if used >= daily_create_limit(source):
        raise ActionPolicyError("今日动作制作额度已用完")


async def check_suppression(
    db: AsyncSession,
    user_id: int,
    *,
    pack_id: int | None,
    semantic_fingerprint: str,
) -> bool:
    """返回 True 表示该创意在近 7 天内被拒绝过（抑制）。"""
    if not semantic_fingerprint:
        return False
    cutoff = datetime.now(UTC) - timedelta(days=REJECTED_PROPOSAL_COOLDOWN_DAYS)
    stmt = select(func.count(ActionProposal.id)).where(
        ActionProposal.user_id == user_id,
        ActionProposal.semantic_fingerprint == semantic_fingerprint,
        ActionProposal.status == "rejected",
        ActionProposal.created_at >= cutoff,
    )
    if pack_id is not None:
        stmt = stmt.where(ActionProposal.pack_id == pack_id)
    count = (await db.execute(stmt)).scalar_one() or 0
    return count > 0


async def check_can_play(
    db: AsyncSession,
    user_id: int,
    action: CompanionAction,
    *,
    source: str = "chat_expression",  # noqa: ARG001 — 保留来源语义供审计，播放不按来源限流
    now: datetime | None = None,  # noqa: ARG001 — 无冷却后暂无时间比较
) -> None:
    if not action.enabled:
        raise ActionPolicyError("该动作已停用")

    if action.status != "succeeded" or not action.video_path:
        raise ActionPolicyError("该动作素材尚未就绪")
