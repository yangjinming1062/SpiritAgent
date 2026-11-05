"""周期扫描与到期任务派发；扫描只发现工作，业务模块拥有执行与持久状态。"""

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

from features.accounts.queries import list_user_ids, lock_active_user_ids, user_is_active
from features.automation.cron_jobs import STANDARD_CRON_KIND
from features.automation.scheduling import (
    DueJob,
    advance_due_jobs,
    delete_consumed_one_shots,
    delete_expired_jobs,
    due_jobs,
)
from features.companion.disturbance import get_disturbance_tier, is_still
from features.companion.intents import claim_companion_intent, enqueue_companion_intent, has_pending_companion_intent
from features.companion.persona_service import onboarded_user_ids
from features.companion.proactive_runtime import (
    can_start_companion_turn,
    get_personality_tags,
    get_user_proactive_record,
    note_outreach_throttle,
)
from features.companion.prompts import CHECK_IN_INTENT_TEXTS
from features.companion.queries import complete_user_ids
from features.configuration.settings.queries import timezone_settings
from features.configuration.settings.values import get_user_setting
from features.conversation.evidence import companion_conversation_users
from features.life.execution import execution_user_ids
from features.life.queries import due_review_user_ids
from features.life.use_cases import pending_presentation_users
from features.memory.memory_learning import list_memory_review_scopes
from features.memory.memory_review import review_scope_if_due
from features.posts.autonomous import autonomous_post_users, request_autonomous_post, resume_autonomous_submissions
from features.topics.store import has_unshared_topics
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.correlation import begin_local_scope
from infrastructure.database import session_scope
from infrastructure.domain_locks import lock_domain
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language, resolve_prompt_text, utc_now
from infrastructure.timezone import parse_timezone
from runtime.background import TaskBag
from runtime.desktop.connection import MANAGER
from runtime.events.outbox_gc import run_outbox_gc
from runtime.memory_scope import MemoryScope
from runtime.presets import COMPANION_PRESET_ID
from runtime.user_maintenance import is_user_in_maintenance, user_request_scope

from workflows.automation.standard_turns import execute_standard_turn
from workflows.life_decisions import consider_life
from workflows.life_execution import run_life_execution
from workflows.life_presentation import evaluate_life_presentation
from workflows.nightly.nightly_activity import run_nightly_pipeline
from workflows.nightly.scheduling import NIGHTLY_UNFINISHED_STATUSES, close_expired_logs, nightly_logs
from workflows.nightly.window import in_nightly_window

logger = get_logger(__name__)

_IGNORED_OUTREACH_MIN_IGNORED_SECONDS = 3600  # 用户持续不理伙伴 1 小时后才有资格触发
_IGNORED_OUTREACH_MIN_SPACING_SECONDS = 3600  # 两次触发之间的最小间距——沉默结束意图后的再触发安全网
# 每个 tick 最多推进 200 个到期 job；按当前时间计算下一次，停摆期间不逐次回追。
_MAX_DUE_PER_TICK = 200


@dataclass(frozen=True)
class ScheduledWork:
    """一次扫描发现的独立工作；扫描名、账户和 key 共同标识单飞身份。"""

    key: str
    run: Callable[[], Awaitable[object]]
    user_id: int | None = None
    # 周期工作首次发现先等待一个间隔；零表示立即执行。元组在首次发现及每次启动后重新抽取。
    interval: float | tuple[float, float] = 0.0

    def next_delay(self) -> float:
        if isinstance(self.interval, tuple):
            return random.uniform(*self.interval)
        return self.interval


@dataclass(frozen=True)
class _Scan:
    interval: float
    discover: Callable[[], Awaitable[list[ScheduledWork]]]


class ScanScheduler:
    """周期扫描的小型调度描述：按周期间隔发现工作，按工作键单飞并记录下次启动时间。"""

    def __init__(self, name: str) -> None:
        self._bag = TaskBag(name)
        self._scans: dict[str, _Scan] = {}
        self._discoveries: dict[str, asyncio.Task[None]] = {}
        self._next_scan: dict[str, float] = {}
        self._running: dict[tuple[str, int | None, str], asyncio.Task[None]] = {}
        self._next_work: dict[tuple[str, int | None, str], float] = {}

    def every(self, name: str, *, interval: float, discover: Callable[[], Awaitable[list[ScheduledWork]]]) -> None:
        self._scans[name] = _Scan(interval, discover)

    async def tick(self) -> None:
        for name, scan in self._scans.items():
            running = self._discoveries.get(name)
            if running is not None and not running.done():
                continue
            now = time.monotonic()
            if now < self._next_scan.get(name, 0.0):
                continue
            self._next_scan[name] = now + scan.interval
            discovery = asyncio.create_task(self._discover(name, scan), name=f"scheduler.scan.{name}")
            self._discoveries[name] = discovery
            self._bag.add(discovery, on_error=partial(_log_task_error, f"scan:{name}"))

    def spawn(self, coro: Coroutine[Any, Any, object], *, name: str, user_id: int | None = None) -> None:
        task = asyncio.create_task(coro, name=f"scheduler.{name}")
        self._bag.add(task, user_id=user_id, on_error=partial(_log_task_error, name))

    async def drain(self) -> None:
        await self._bag.drain()

    async def _discover(self, name: str, scan: _Scan) -> None:
        begin_local_scope()
        try:
            items = await scan.discover()
        except Exception:
            logger.exception("scheduler: scan discovery failed", extra={"scan": name})
            return
        now = time.monotonic()
        discovered = {(name, item.user_id, item.key) for item in items}
        # 暂时不符合资格不撤销仍有效的冷却；缺席且已过期的工作才回收。
        for key in [
            key
            for key, deadline in self._next_work.items()
            if key[0] == name and key not in discovered and deadline <= now
        ]:
            self._next_work.pop(key, None)
        for item in items:
            key = (name, item.user_id, item.key)
            running = self._running.get(key)
            if running is not None and not running.done():
                continue
            deadline = self._next_work.get(key)
            if deadline is None:
                deadline = self._next_work[key] = now + item.next_delay()
            if now < deadline or (item.user_id is not None and is_user_in_maintenance(item.user_id)):
                continue
            self._next_work[key] = now + item.next_delay()
            label = f"{name}:{item.user_id}:{item.key}"
            task = asyncio.create_task(self._run_work(item), name=f"scheduler.{label}")
            self._running[key] = task
            self._bag.add(task, user_id=item.user_id, on_error=partial(_log_task_error, label))
            task.add_done_callback(partial(self._discard, key))

    def _discard(self, key: tuple[str, int | None, str], task: asyncio.Task[None]) -> None:
        if self._running.get(key) is task:
            self._running.pop(key, None)

    async def _run_work(self, item: ScheduledWork) -> None:
        # 后台工作跨越多个 tick，日志使用独立请求身份。
        begin_local_scope()
        await item.run()


# 扫描只发现并提交工作；同名发现与同一工作键各自单飞。
_SCANS = ScanScheduler("scheduler.cron")


def _log_task_error(name: str, task: asyncio.Task) -> None:
    """旁路 task 失败落日志不冒泡——扫描失败不该连坐停掉定时派发。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("cron: background task failed", exc_info=exc, extra={"task": name})


async def drain() -> None:
    """取消并 await 所有后台 task；由 bootstrap/lifecycle.py 的停机 drain 调用，避免 SIGTERM 在 db.commit() 中途被 engine 释放。"""
    await _SCANS.drain()


async def _select_due_jobs() -> list[DueJob]:
    now = utc_now()
    async with session_scope() as db:
        active_users = await list_user_ids(db, active_only=True)
        return await due_jobs(db, now, MANAGER.local_user_ids(), active_users, limit=_MAX_DUE_PER_TICK + 1)


async def _bulk_cas_advance(due_jobs: list[DueJob], now: datetime) -> list[DueJob]:
    async with session_scope() as db:
        # 与编辑共用调度锁，再锁账户；旧快照不能在编辑撤销意图后重新交接。
        for user_id in sorted({job.user_id for job in due_jobs}):
            await lock_domain(db, user_id, "automation")
        active_users = await lock_active_user_ids(db, {job.user_id for job in due_jobs})
        fired = await advance_due_jobs(db, due_jobs, now, active_users)
        for job in sorted(fired, key=lambda row: (row.user_id, row.id)):
            if job.kind != STANDARD_CRON_KIND:
                await enqueue_companion_intent(
                    db, job.user_id, job.prompt, source_kind="cron", cron_job_id=job.id, expires_at=job.expires_at
                )
        await delete_consumed_one_shots(db, fired)
        await db.commit()
    return fired


async def _advance_due_jobs(due_jobs: list[DueJob], now: datetime) -> None:
    """批量推进调度游标：standard 启动独立任务回合，special 在保存等待意图后按用户认领。"""
    expired_ids = {job.id for job in due_jobs if job.expires_at is not None and job.expires_at <= now}
    if expired_ids:
        async with session_scope() as db:
            await delete_expired_jobs(db, expired_ids, now)
            await db.commit()

    online_users = set(MANAGER.local_user_ids())
    deferred_users = {
        job.user_id
        for job in due_jobs
        if job.id not in expired_ids
        and job.kind != STANDARD_CRON_KIND
        and job.expires_at is not None
        and job.user_id in online_users
    }
    still_results = await asyncio.gather(*(is_still(user_id) for user_id in deferred_users), return_exceptions=True)
    still_by_user: dict[int, bool] = {}
    for user_id, result in zip(deferred_users, still_results, strict=True):
        if isinstance(result, BaseException):
            # 档位读不到时按静止处理：问候保持到期，下个 tick 重试。
            logger.warning("cron: disturbance tier lookup failed", exc_info=result, extra={"user_id": user_id})
            still_by_user[user_id] = True
        else:
            still_by_user[user_id] = result
    # 带 expires_at 的夜间主动问候在用户离线或静止档时保持 due；普通 special cron 仍按原周期语义推进。
    deliverable = [
        job
        for job in due_jobs
        if job.id not in expired_ids
        and (
            job.kind == STANDARD_CRON_KIND
            or job.expires_at is None
            or (job.user_id in online_users and not still_by_user.get(job.user_id, True))
        )
    ]
    if not deliverable:
        return
    fired = await _bulk_cas_advance(deliverable, now)
    kicks: list[tuple[int, str, Coroutine[Any, Any, object]]] = [
        (
            job.user_id,
            f"kick:{job.id}",
            execute_standard_turn(
                job.user_id,
                job.id,
                name=job.name,
                prompt=job.prompt,
                conversation_id=job.conversation_id,
                one_shot=job.one_shot,
            ),
        )
        for job in fired
        if job.kind == STANDARD_CRON_KIND
    ]
    kicks.extend(
        (user_id, f"claim:{user_id}", claim_companion_intent(user_id))
        for user_id in {job.user_id for job in fired if job.kind != STANDARD_CRON_KIND}
    )
    for user_id, name, coro in kicks:
        _SCANS.spawn(coro, name=name, user_id=user_id)


async def _discover_companion_waits() -> list[ScheduledWork]:
    return [
        ScheduledWork("claim", partial(claim_companion_intent, user_id), user_id)
        for user_id in MANAGER.local_user_ids()
    ]


def _outreach_eligible_now(user_id: int) -> tuple[bool, int]:
    """先检查纯内存条件，命中后才读库；返回是否继续及被冷落分钟数。"""
    cur_time = time.monotonic()
    rec = get_user_proactive_record(user_id)
    if not can_start_companion_turn(user_id) or rec.last_user_contact_ts == 0.0:
        return False, 0
    ignored = cur_time - rec.last_user_contact_ts
    if ignored < _IGNORED_OUTREACH_MIN_IGNORED_SECONDS:
        return False, 0
    if cur_time - rec.last_outreach_ts < _IGNORED_OUTREACH_MIN_SPACING_SECONDS:
        return False, 0
    return True, round(ignored / 60)


async def _maybe_run_ignored_outreach(user_id: int) -> None:
    """有可分享发现时低频考虑联系；无发现时保留常规档的性格问候。"""
    eligible, ignored_minutes = _outreach_eligible_now(user_id)
    if not eligible:
        return
    # 档位、等待意图与性格标签的读库放在所有内存条件之后。
    async with session_scope() as db:
        tier = await get_disturbance_tier(user_id, db=db)
        if tier == "still" or await has_pending_companion_intent(db, user_id):
            return
        has_topics = await has_unshared_topics(db, MemoryScope(user_id, COMPANION_PRESET_ID), utc_now())
        if not has_topics and (tier != "normal" or "粘人" not in await get_personality_tags(db, user_id)):
            return
        language = resolve_language(await get_user_setting(db, user_id, "language"))

    prompt = json.dumps(
        {
            "kind": "low_frequency_check_in",
            "minutes_since_last_user_interaction": ignored_minutes,
            "has_unshared_topics": has_topics,
            "intent": resolve_prompt_text(CHECK_IN_INTENT_TEXTS, language),
        },
        ensure_ascii=False,
    )
    async with user_request_scope(user_id) as acquired:
        if not acquired:
            return
        note_outreach_throttle(user_id)
        async with session_scope() as db:
            await enqueue_companion_intent(db, user_id, prompt, source_kind="checkin")
            await db.commit()
    logger.info("cron: ignored-outreach turn requested", extra={"user_id": user_id, "ignored_minutes": ignored_minutes})


async def _discover_ignored_outreach() -> list[ScheduledWork]:
    return [
        ScheduledWork("consider", partial(_maybe_run_ignored_outreach, user_id), user_id)
        for user_id in MANAGER.local_user_ids()
        if not is_user_in_maintenance(user_id)
    ]


async def _discover_gc() -> list[ScheduledWork]:
    return [ScheduledWork("outbox", run_outbox_gc)]


async def run_tick() -> None:
    """登记周期扫描并派发到期 job；扫描在后台执行，不阻塞 tick。"""
    _SCANS.every("companion_waits", interval=0, discover=_discover_companion_waits)
    _SCANS.every("ignored_outreach", interval=0, discover=_discover_ignored_outreach)
    _SCANS.every("life_state", interval=60, discover=_discover_life_work)
    _SCANS.every(
        "nightly_activity", interval=SETTINGS.nightly_scan_interval_seconds, discover=_discover_nightly_activity
    )
    _SCANS.every("post_impulse", interval=0, discover=_discover_autonomous_posts)
    _SCANS.every("memory_review", interval=600, discover=_discover_memory_reviews)
    _SCANS.every("outbox_gc", interval=900, discover=_discover_gc)
    await _SCANS.tick()

    now = utc_now()
    due_jobs = await _select_due_jobs()
    if len(due_jobs) > _MAX_DUE_PER_TICK:
        logger.warning(
            "cron: tick over cap, deferred to next tick", extra={"due_count": len(due_jobs), "cap": _MAX_DUE_PER_TICK}
        )
    due_jobs = due_jobs[:_MAX_DUE_PER_TICK]
    if not due_jobs:
        return
    async with AsyncExitStack() as stack:
        leases: dict[int, bool] = {}
        for job in due_jobs:
            uid = int(job.user_id)
            if uid not in leases:
                leases[uid] = await stack.enter_async_context(user_request_scope(uid))
        await _advance_due_jobs([job for job in due_jobs if leases[int(job.user_id)]], now)


async def _discover_life_work() -> list[ScheduledWork]:
    now = utc_now()
    async with session_scope() as db:
        active_users = await list_user_ids(db, active_only=True)
        completed = await complete_user_ids(db, active_users)
        onboarded = await onboarded_user_ids(db, completed)
        users = await due_review_user_ids(db, onboarded, now)
        executors = await execution_user_ids(db, now)
        presentation_users = await pending_presentation_users(db)
    work = [
        ScheduledWork("review", partial(consider_life, user_id), user_id)
        for user_id in sorted(users)
        if not is_user_in_maintenance(user_id)
    ]
    # 已提交步骤仍需核对；未受理步骤由执行入口复核引导状态。
    work.extend(
        ScheduledWork("execute", partial(run_life_execution, user_id), user_id)
        for user_id in sorted(executors & set(completed))
        if not is_user_in_maintenance(user_id)
    )
    work.extend(
        ScheduledWork("present", partial(evaluate_life_presentation, user_id), user_id)
        for user_id in sorted((presentation_users | set(MANAGER.local_user_ids())) & set(completed))
        if not is_user_in_maintenance(user_id)
    )
    return work


async def _run_memory_review(scope: MemoryScope) -> None:
    """没有可用 LLM 配置时跳过，不记录审阅成功或失败。"""
    async with session_scope() as db:
        if not await user_is_active(db, scope.user_id):
            return
        llm_config = await resolve_user_llm_config(db, scope.user_id, AiTaskKey.memory_review)
    if not llm_config.is_configured:
        logger.info("memory_review: skipped, missing llm config", extra={"user_id": scope.user_id})
        return
    await review_scope_if_due(scope, llm_config=llm_config)


async def _discover_memory_reviews() -> list[ScheduledWork]:
    """按域发现待审材料；在途域不重复提交，不阻塞其他用户的后续扫描。"""
    async with session_scope() as db:
        scopes = await list_memory_review_scopes(db)
    return [
        ScheduledWork(scope.system_preset_id, partial(_run_memory_review, scope), scope.user_id)
        for scope in scopes
        if not is_user_in_maintenance(scope.user_id)
    ]


async def _close_nightly_logs(log_ids: list[int]) -> None:
    async with session_scope() as db:
        await close_expired_logs(db, log_ids)
        await db.commit()


async def _discover_nightly_activity() -> list[ScheduledWork]:
    now = utc_now()
    async with session_scope() as db:
        active_users = await list_user_ids(db, active_only=True)
        eligible_users = await companion_conversation_users(db, active_users, COMPANION_PRESET_ID)
        eligible_users.update(await complete_user_ids(db, active_users))
        rows = await timezone_settings(db, eligible_users)
        zones = {user_id: zone for user_id, name in rows if (zone := parse_timezone(name)) is not None}
        if not zones:
            return []
        latest = {user_id: now.astimezone(zone).date() - timedelta(days=1) for user_id, zone in zones.items()}
        logs = await nightly_logs(db, list(zones), since=min(latest.values()))
    work = []
    for user_id, zone in zones.items():
        if is_user_in_maintenance(user_id):
            continue
        target = latest[user_id]
        user_logs = logs.get(user_id, [])
        expired = [
            log.id for log in user_logs if log.target_date < target and log.status in NIGHTLY_UNFINISHED_STATUSES
        ]
        if expired:
            work.append(ScheduledWork("close", partial(_close_nightly_logs, expired), user_id))
        current = next((log for log in user_logs if log.target_date == target), None)
        if current is not None and current.is_completed:
            continue
        resuming = current is not None and current.status in NIGHTLY_UNFINISHED_STATUSES
        if not in_nightly_window(now, zone) and not resuming:
            continue
        work.append(
            ScheduledWork(
                "pipeline",
                partial(_run_nightly_activity, MemoryScope(user_id, COMPANION_PRESET_ID), target),
                user_id,
            )
        )
    return work


async def _run_nightly_activity(scope: MemoryScope, target: date) -> None:
    async with session_scope() as db:
        if not await user_is_active(db, scope.user_id):
            return
        zone = parse_timezone(await get_user_setting(db, scope.user_id, "timezone"))
    if zone is None:
        return
    # 扫描与实际启动之间可能跨过窗口或本地午夜。
    now = utc_now()
    if target != now.astimezone(zone).date() - timedelta(days=1):
        return
    await run_nightly_pipeline(scope, target)


async def _discover_autonomous_posts() -> list[ScheduledWork]:
    work = [ScheduledWork("resume", resume_autonomous_submissions)]
    work.extend(
        ScheduledWork("decide", partial(request_autonomous_post, user_id), user_id, interval=(45 * 60, 90 * 60))
        for user_id in await autonomous_post_users()
    )
    return work
