import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta

from components import SETTINGS, session_scope, utc_now
from modules.auth import User
from modules.companion import (
    MAX_COMPANION_FAILURES,
    CompanionIntent,
    CompanionIntentView,
    CompanionTurnRequest,
    CompanionWaitRequest,
    CompanionWakeEvent,
    companion_cron_source_key,
)
from modules.conversation import CompanionReply, Conversation, Message
from modules.ws import COMPANION_TURN_EVENT, emit_ws_event
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .affect_emit import append_companion_message
from .disturbance import get_disturbance_tier
from .proactive_runtime import can_start_companion_turn, get_user_proactive_record, note_outreach_throttle

# 待处理意图、回合间隔与超时上限走 SETTINGS 动态配置；队列租约是回合超时的实现细节，留在代码。
_QUEUE_LEASE_SECONDS: int = 180
_ACTIVE_STATUSES: tuple[str, ...] = ("waiting", "queued", "running")
_INTENT_RETENTION: timedelta = timedelta(days=30)
_TURN_PLAN: ContextVar["CompanionTurnPlan | None"] = ContextVar("companion_turn_plan", default=None)


@dataclass
class CompanionTurnPlan:
    user_id: int
    intent_id: int
    expires_at: datetime
    followup: CompanionWaitRequest | None = None


@contextmanager
def companion_turn_plan(user_id: int, intent_id: int, expires_at: datetime) -> Iterator[CompanionTurnPlan]:
    plan = CompanionTurnPlan(user_id, intent_id, expires_at)
    token = _TURN_PLAN.set(plan)
    try:
        yield plan
    finally:
        _TURN_PLAN.reset(token)


async def _lock_user(db: AsyncSession, user_id: int) -> None:
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


async def latest_user_message_id(db: AsyncSession, user_id: int) -> int:
    return (
        await db.execute(
            select(func.max(Message.id))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id, Conversation.is_automation.is_(False), Message.role == "user"),
        )
    ).scalar_one() or 0


async def enqueue_companion_intent(
    db: AsyncSession,
    user_id: int,
    intent: str,
    *,
    source_key: str,
    expires_at: datetime | None = None,
) -> None:
    """在生产者事务中保存触发意图；同来源尚未完成时合并，不重复堆积。"""
    if not intent.strip():
        return
    await _lock_user(db, user_id)
    now = utc_now()
    if expires_at is not None and expires_at <= now:
        return
    existing = (
        await db.execute(
            select(CompanionIntent.id).where(
                CompanionIntent.user_id == user_id,
                CompanionIntent.source_key == source_key,
                CompanionIntent.status.in_(_ACTIVE_STATUSES),
                CompanionIntent.expires_at > now,
            ),
        )
    ).first()
    if existing is not None:
        return
    db.add(
        CompanionIntent(
            user_id=user_id,
            intent=intent,
            source_key=source_key,
            not_before_at=now,
            wake_at=now,
            expires_at=expires_at or now + timedelta(days=1),
        ),
    )
    await db.flush()


def _apply_wait(row: CompanionIntent, request: CompanionWaitRequest, now: datetime) -> None:
    row.intent = request.intent
    row.status = "waiting"
    row.not_before_at = now + timedelta(seconds=60)
    row.wake_at = now + timedelta(seconds=request.after_seconds) if request.after_seconds is not None else None
    row.wake_event = request.wake_on
    row.event_received_at = None
    row.expires_at = now + timedelta(seconds=request.expires_seconds)
    row.lease_token = None
    row.lease_until = None
    row.failure_count = 0
    row.last_error = None


async def set_companion_wait(
    user_id: int,
    request: CompanionWaitRequest,
    intent_id: int | None = None,
) -> int:
    plan = _TURN_PLAN.get()
    if plan is not None:
        if plan.user_id != user_id or intent_id not in (None, plan.intent_id):
            raise ValueError("A proactive turn may only defer its current intent")
        if utc_now() + timedelta(seconds=request.after_seconds or 60) >= plan.expires_at:
            raise ValueError("The requested wake time is outside the original intent's validity window")
        # 主动回合内只暂存计划，成功终态时与消息原子提交；取消或失败不能留下幽灵跟进。
        plan.followup = request
        return plan.intent_id
    async with session_scope() as db:
        await _lock_user(db, user_id)
        row = (
            (
                await db.execute(
                    select(CompanionIntent).where(CompanionIntent.user_id == user_id, CompanionIntent.id == intent_id),
                )
            ).scalar_one_or_none()
            if intent_id is not None
            else None
        )
        if intent_id is not None and row is None:
            raise ValueError("Companion intent not found")
        now = utc_now()
        if row is None or row.status not in _ACTIVE_STATUSES or row.expires_at <= now:
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(CompanionIntent)
                    .where(
                        CompanionIntent.user_id == user_id,
                        CompanionIntent.status.in_(_ACTIVE_STATUSES),
                        CompanionIntent.expires_at > now,
                    ),
                )
            ).scalar_one()
            if count >= SETTINGS.companion_max_pending_intents:
                raise ValueError(f"Pending companion intent limit ({SETTINGS.companion_max_pending_intents}) reached")
        if row is None:
            row = CompanionIntent(user_id=user_id)
            db.add(row)
        _apply_wait(row, request, now)
        await db.flush()
        result = row.id
        await db.commit()
        return result


async def cancel_companion_wait(user_id: int, intent_id: int) -> bool:
    plan = _TURN_PLAN.get()
    if plan is not None:
        if plan.user_id != user_id or plan.intent_id != intent_id:
            raise ValueError("A proactive turn may only complete its current intent")
        plan.followup = None
        return True
    async with session_scope() as db:
        await _lock_user(db, user_id)
        result = await db.execute(
            update(CompanionIntent)
            .where(
                CompanionIntent.user_id == user_id,
                CompanionIntent.id == intent_id,
                CompanionIntent.status.in_((*_ACTIVE_STATUSES, "failed")),
            )
            .values(status="cancelled", lease_token=None, lease_until=None)
            .returning(CompanionIntent.id),
        )
        cancelled = result.scalar_one_or_none() is not None
        await db.commit()
        return cancelled


async def list_companion_intents(db: AsyncSession, user_id: int) -> list[CompanionIntentView]:
    now = utc_now()
    rows = (
        await db.execute(
            select(CompanionIntent)
            .where(
                CompanionIntent.user_id == user_id,
                or_(
                    CompanionIntent.status.in_(_ACTIVE_STATUSES) & (CompanionIntent.expires_at > now),
                    (CompanionIntent.status == "failed") & (CompanionIntent.updated_at >= now - _INTENT_RETENTION),
                ),
            )
            .order_by(CompanionIntent.status == "failed", CompanionIntent.created_at.desc(), CompanionIntent.id.desc())
            .limit(32),
        )
    ).scalars()
    return [CompanionIntentView.model_validate(row) for row in rows]


async def invalidate_cron_companion_intents(db: AsyncSession, user_id: int, job_id: int) -> None:
    """源任务变更与待兑现意图失效共用事务；已开始的执行保留待核对事实。"""
    await _lock_user(db, user_id)
    scope = (CompanionIntent.user_id == user_id) & (CompanionIntent.source_key == companion_cron_source_key(job_id))
    await db.execute(
        update(CompanionIntent)
        .where(scope, CompanionIntent.status.in_(("waiting", "queued")))
        .values(status="cancelled", lease_token=None, lease_until=None),
    )
    await db.execute(
        update(CompanionIntent)
        .where(scope, CompanionIntent.status == "running")
        .values(
            status="failed",
            lease_token=None,
            lease_until=None,
            last_error="Source schedule changed during execution; verify previous tool effects before rescheduling.",
        ),
    )


async def queue_companion_intent(user_id: int, event: CompanionWakeEvent | None = None) -> bool:
    """轻量事件合并与到期认领；等待状态持久化，不依赖常驻 LLM 或长 sleep。"""
    async with session_scope() as db:
        await _lock_user(db, user_id)
        now = utc_now()
        scope = CompanionIntent.user_id == user_id
        await db.execute(
            update(CompanionIntent)
            .where(scope, CompanionIntent.status.in_(("waiting", "queued")), CompanionIntent.expires_at <= now)
            .values(status="expired", lease_token=None, lease_until=None),
        )
        await db.execute(
            update(CompanionIntent)
            .where(scope, CompanionIntent.status == "queued", CompanionIntent.lease_until <= now)
            .values(status="waiting", lease_token=None, lease_until=None),
        )
        # 进程在工具执行中崩溃时无法确认副作用；保留失败事实供下次用户回合核对，不能盲目重放。
        await db.execute(
            update(CompanionIntent)
            .where(
                scope,
                CompanionIntent.status == "running",
                or_(CompanionIntent.lease_until <= now, CompanionIntent.expires_at <= now),
            )
            .values(
                status="failed",
                lease_token=None,
                lease_until=None,
                last_error="Interrupted run; verify any previous tool effects before rescheduling.",
            ),
        )
        await db.execute(
            delete(CompanionIntent).where(
                scope,
                CompanionIntent.status.not_in(_ACTIVE_STATUSES),
                CompanionIntent.updated_at < now - _INTENT_RETENTION,
            ),
        )
        if event is not None:
            await db.execute(
                update(CompanionIntent)
                .where(
                    scope,
                    CompanionIntent.status == "waiting",
                    CompanionIntent.wake_event == event,
                    CompanionIntent.event_received_at.is_(None),
                )
                .values(event_received_at=now),
            )
        busy = (
            await db.execute(
                select(CompanionIntent.id).where(scope, CompanionIntent.status.in_(("queued", "running"))).limit(1),
            )
        ).first() is not None
        last_attempt = (await db.execute(select(func.max(CompanionIntent.last_attempt_at)).where(scope))).scalar_one()
        cooling = last_attempt is not None and now - last_attempt < timedelta(
            seconds=SETTINGS.companion_min_turn_interval_seconds,
        )
        eligible = can_start_companion_turn(user_id) and await get_disturbance_tier(user_id, db=db) != "still"
        if busy or cooling or not eligible:
            await db.commit()
            return False
        row = (
            await db.execute(
                select(CompanionIntent)
                .where(
                    scope,
                    CompanionIntent.status == "waiting",
                    CompanionIntent.expires_at > now,
                    CompanionIntent.not_before_at <= now,
                    or_(CompanionIntent.wake_at <= now, CompanionIntent.event_received_at.is_not(None)),
                )
                .order_by(CompanionIntent.not_before_at, CompanionIntent.id)
                .limit(1),
            )
        ).scalar_one_or_none()
        if row is None:
            await db.commit()
            return False
        row.status = "queued"
        row.lease_token = secrets.token_hex(16)
        row.lease_until = now + timedelta(seconds=_QUEUE_LEASE_SECONDS)
        row.last_attempt_at = now
        emit_ws_event(
            db,
            user_id=user_id,
            event_type=COMPANION_TURN_EVENT,
            payload=CompanionTurnRequest(intent_id=row.id, lease_token=row.lease_token).model_dump(),
        )
        await db.commit()
        return True


async def begin_companion_intent(user_id: int, request: CompanionTurnRequest) -> CompanionIntentView | None:
    async with session_scope() as db:
        await _lock_user(db, user_id)
        row = (
            await db.execute(
                select(CompanionIntent).where(
                    CompanionIntent.user_id == user_id,
                    CompanionIntent.id == request.intent_id,
                    CompanionIntent.status == "queued",
                    CompanionIntent.lease_token == request.lease_token,
                    CompanionIntent.expires_at > utc_now(),
                    CompanionIntent.lease_until > utc_now(),
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if not can_start_companion_turn(user_id) or await get_disturbance_tier(user_id, db=db) == "still":
            row.status = "waiting"
            row.not_before_at = utc_now() + timedelta(seconds=60)
            row.lease_token = None
            row.lease_until = None
            await db.commit()
            return None
        row.status = "running"
        row.lease_until = utc_now() + timedelta(seconds=SETTINGS.companion_turn_timeout_seconds + 30)
        await db.commit()
        return CompanionIntentView.model_validate(row)


async def finish_companion_intent(
    user_id: int,
    request: CompanionTurnRequest,
    *,
    reply: CompanionReply | None = None,
    followup: CompanionWaitRequest | None = None,
    contact_revision: int,
    user_message_id: int,
    error: str | None = None,
    interrupted: bool = False,
    tools_started: bool = False,
) -> bool:
    async with session_scope() as db:
        await _lock_user(db, user_id)
        row = (
            await db.execute(
                select(CompanionIntent).where(
                    CompanionIntent.user_id == user_id,
                    CompanionIntent.id == request.intent_id,
                    CompanionIntent.status == "running",
                    CompanionIntent.lease_token == request.lease_token,
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        now = utc_now()
        tier = await get_disturbance_tier(user_id, db=db)
        context_changed = (
            get_user_proactive_record(user_id).contact_revision != contact_revision
            or await latest_user_message_id(db, user_id) != user_message_id
            or not can_start_companion_turn(user_id)
            or tier == "still"
            or reply is not None
            and tier != "autonomous"
            and any(bubble.type == "voice" for bubble in reply.bubbles)
        )
        delivered = False
        if row.expires_at <= now:
            row.status = "failed" if tools_started else "expired"
            if tools_started:
                row.last_error = "Intent expired during execution; verify previous tool effects before rescheduling."
        elif error or interrupted or context_changed:
            row.last_error = (error or "User activity or availability changed during the turn")[:1000]
            if tools_started:
                row.status = "failed"
                row.last_error += "; verify previous tool effects before rescheduling."
            else:
                if error and not interrupted:
                    row.failure_count = min(row.failure_count + 1, MAX_COMPANION_FAILURES)
                row.status = "failed" if row.failure_count >= MAX_COMPANION_FAILURES else "waiting"
                row.not_before_at = now + timedelta(
                    seconds=60 if interrupted or context_changed else 300 * 2 ** max(0, row.failure_count - 1),
                )
        else:
            if reply:
                await append_companion_message(db, user_id, reply)
                delivered = True
            if followup is None:
                row.status = "completed"
            else:
                original_expiry = row.expires_at
                _apply_wait(row, followup, now)
                row.expires_at = min(original_expiry, row.expires_at)
                if row.not_before_at >= row.expires_at or row.wake_at is not None and row.wake_at >= row.expires_at:
                    row.status = "expired"
        row.lease_token = None
        row.lease_until = None
        await db.commit()
    if delivered:
        note_outreach_throttle(user_id)
    return delivered
