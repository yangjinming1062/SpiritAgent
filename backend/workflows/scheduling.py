"""定时触发、记忆审阅、独立动态与夜间活动的跨业务编排。"""

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

from features.accounts.queries import list_user_ids, lock_active_user_ids, user_is_active
from features.automation.cron_jobs import STANDARD_CRON_KIND
from features.automation.scheduling import DueJob, advance_due_jobs, delete_expired_jobs, due_jobs
from features.companion.disturbance import get_disturbance_tier, is_still
from features.companion.intent_models import companion_cron_source_key
from features.companion.intents import (
    claim_companion_intent,
    enqueue_companion_intent,
    has_pending_companion_intent,
    queue_companion_intent,
)
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
from features.life.queries import due_review_user_ids
from features.memory.memory_learning import list_memory_review_scopes
from features.memory.memory_review import memory_review_backed_off, review_memories
from features.posts.autonomous import scan_autonomous_posts
from features.topics.store import has_unshared_topics
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.correlation import begin_local_scope
from infrastructure.database import session_scope
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
from workflows.nightly.nightly_activity import run_nightly_pipeline
from workflows.nightly.scheduling import close_expired_logs, unfinished_logs
from workflows.nightly.window import in_nightly_window

logger = get_logger(__name__)

_BG = TaskBag("scheduler.cron")

# 扫描只发现并提交工作；同名扫描与同域流水线各自单飞。
_SCANS: dict[str, asyncio.Task] = {}
_SCOPE_TASKS: dict[tuple[str, MemoryScope], asyncio.Task] = {}

# 每作用域最近一次成功审阅的时间戳：进程本地，遵守单进程部署边界。
_LAST_MEMORY_REVIEW: dict[MemoryScope, float] = {}

# per-user 最近一次成功的 nightly pipeline 运行的目标本地日。
_LAST_NIGHTLY_RUN: dict[MemoryScope, date] = {}

# 待审作用域每 10 分钟扫描；成功后按配置节流，失败由记忆域退避。
_LAST_MEMORY_REVIEW_SCAN: float = 0.0
_MEMORY_REVIEW_SCAN_INTERVAL_SECONDS: int = 600

# nightly activity 扫描的外层节流。
_LAST_NIGHTLY_SCAN: float = 0.0

# outbox gc 扫描的外层节流（每 15 分钟运行一次）。
_LAST_OUTBOX_GC_SCAN: float = 0.0
_OUTBOX_GC_INTERVAL_SECONDS: int = 900
_LAST_LIFE_SCAN: float = 0.0
_LIFE_SCAN_INTERVAL_SECONDS: int = 60

_IGNORED_OUTREACH_MIN_IGNORED_SECONDS = 3600  # 用户持续不理伙伴 1 小时后才有资格触发
_IGNORED_OUTREACH_MIN_SPACING_SECONDS = 3600  # 两次触发之间的最小间距——沉默结束意图后的再触发安全网


def invalidate_user_scheduler_state(user_id: int) -> None:
    """覆盖恢复后丢弃从旧数据计算出的调度节流镜像。"""
    for state in (_LAST_MEMORY_REVIEW, _LAST_NIGHTLY_RUN):
        for scope in list(state):
            if scope.user_id == user_id:
                state.pop(scope, None)


# 每个 tick 最多推进 200 个到期 job；按当前时间计算下一次，停摆期间不逐次回追。
_MAX_DUE_PER_TICK = 200


async def drain() -> None:
    """取消并 await 所有后台 task，容忍 CancelledError；由 bootstrap/lifecycle.py 的停机 drain 调用，避免 SIGTERM 在 db.commit() 中途被 engine 释放。"""
    await _BG.drain()


def _log_task_error(name: str, task: asyncio.Task) -> None:
    """旁路 task 失败落日志不冒泡——_tick 的异常会杀死 BackgroundTask 曝光派发路径 bug，kickoff / 扫描失败不该连坐停掉定时派发。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("cron: background task failed", exc_info=exc, extra={"task": name})


def _spawn_scan(name: str, factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """扫描移出 tick 关键路径；同名扫描仍在飞则跳过本轮。"""
    running = _SCANS.get(name)
    if running is not None and not running.done():
        logger.warning("cron: scan still in flight, skipping", extra={"scan": name})
        return

    async def _scoped() -> None:
        # 扫描是独立于 tick 的工作单元，mint 自己的 request_id 才能把它跨越几轮 tick 的日志归到一起。
        begin_local_scope()
        await factory()

    task = asyncio.create_task(_scoped(), name=f"scheduler.{name}")
    _SCANS[name] = task
    _BG.add(task, on_error=partial(_log_task_error, name))


def _spawn_scope_task(name: str, scope: MemoryScope, factory: Callable[[], Coroutine[Any, Any, object]]) -> None:
    """同域流水线单飞，任务由调度器与用户维护边界共同收敛。"""
    key = (name, scope)
    if (running := _SCOPE_TASKS.get(key)) is not None and not running.done():
        return

    async def _scoped() -> None:
        begin_local_scope()
        await factory()

    task = asyncio.create_task(_scoped(), name=f"scheduler.{name}.{scope}")
    _SCOPE_TASKS[key] = task

    def _discard(completed: asyncio.Task) -> None:
        if _SCOPE_TASKS.get(key) is completed:
            _SCOPE_TASKS.pop(key, None)

    task.add_done_callback(_discard)
    _BG.add(task, user_id=scope.user_id, on_error=partial(_log_task_error, f"{name}:{scope}"))


async def _select_due_jobs() -> list[DueJob]:
    now = utc_now()
    async with session_scope() as db:
        active_users = await list_user_ids(db, active_only=True)
        return await due_jobs(db, now, MANAGER.local_user_ids(), active_users, limit=_MAX_DUE_PER_TICK + 1)


async def _bulk_cas_advance(due_jobs: list[DueJob], now: datetime) -> list[DueJob]:
    async with session_scope() as db:
        active_users = await lock_active_user_ids(db, {job.user_id for job in due_jobs})
        fired = await advance_due_jobs(db, due_jobs, now, active_users)
        for job in sorted(fired, key=lambda row: (row.user_id, row.id)):
            if job.kind != STANDARD_CRON_KIND:
                await enqueue_companion_intent(
                    db,
                    job.user_id,
                    job.prompt,
                    source_key=companion_cron_source_key(job.id),
                    expires_at=job.expires_at,
                )
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
    still_results = await asyncio.gather(
        *(is_still(user_id) for user_id in deferred_users),
        return_exceptions=True,
    )
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
    kicks: list[tuple[int, str, Coroutine[Any, Any, None]]] = [
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
        task = asyncio.create_task(coro)
        _BG.add(task, user_id=user_id, on_error=partial(_log_task_error, name))


async def _scan_companion_waits() -> None:
    for user_id in MANAGER.local_user_ids():
        await claim_companion_intent(user_id)


async def _maybe_run_ignored_outreach() -> None:
    """有可分享发现时低频考虑联系；无发现时保留常规档的性格问候。"""
    cur_time = time.monotonic()
    for uid in MANAGER.local_user_ids():
        if is_user_in_maintenance(uid):
            continue
        rec = get_user_proactive_record(uid)
        if not can_start_companion_turn(uid):
            continue
        # 0 = 进程启动以来用户还没互动过——没有「被冷落」的基准，跳过。
        if rec.last_user_contact_ts == 0.0:
            continue
        ignored = cur_time - rec.last_user_contact_ts
        if ignored < _IGNORED_OUTREACH_MIN_IGNORED_SECONDS:
            continue
        if cur_time - rec.last_outreach_ts < _IGNORED_OUTREACH_MIN_SPACING_SECONDS:
            continue
        # 档位、等待意图与性格标签的读库放在所有内存条件之后。
        async with session_scope() as db:
            tier = await get_disturbance_tier(uid, db=db)
            if tier == "still" or await has_pending_companion_intent(db, uid):
                continue
            has_topics = await has_unshared_topics(db, MemoryScope(uid, COMPANION_PRESET_ID), utc_now())
            if not has_topics and (tier != "normal" or "粘人" not in await get_personality_tags(db, uid)):
                continue
            language = resolve_language(await get_user_setting(db, uid, "language"))

        ignored_minutes = round(ignored / 60)
        prompt = json.dumps(
            {
                "kind": "low_frequency_check_in",
                "minutes_since_last_user_interaction": ignored_minutes,
                "has_unshared_topics": has_topics,
                "intent": resolve_prompt_text(CHECK_IN_INTENT_TEXTS, language),
            },
            ensure_ascii=False,
        )
        async with user_request_scope(uid) as acquired:
            if not acquired:
                continue
            note_outreach_throttle(uid)
            async with session_scope() as db:
                await enqueue_companion_intent(db, uid, prompt, source_key="checkin:ignored")
                await db.commit()
            await queue_companion_intent(uid)
        logger.info(
            "cron: ignored-outreach turn requested",
            extra={"user_id": uid, "ignored_minutes": ignored_minutes},
        )


async def run_tick() -> None:
    """为到期 job CAS 推进 next_run_at；special 持久化等待意图，standard 直接启动独立任务回合。"""
    now = utc_now()
    # 慢扫描与 cron-job 派发独立——不能用 ``if not due_jobs`` gate，否则没有 cron job 的安装永远不会触发记忆维护/GC。
    _spawn_scan("memory_review", lambda: _maybe_run_memory_review(now))
    _spawn_scan("nightly_activity", lambda: _maybe_run_autonomous_activity(now))
    _spawn_scan("outbox_gc", lambda: _maybe_run_outbox_gc(now))
    _spawn_scan("ignored_outreach", _maybe_run_ignored_outreach)
    _spawn_scan("companion_waits", _scan_companion_waits)
    _spawn_scan("post_impulse", scan_autonomous_posts)
    _spawn_scan("life_state", _maybe_run_life_scan)
    due_jobs = await _select_due_jobs()
    if len(due_jobs) > _MAX_DUE_PER_TICK:
        logger.warning(
            "cron: tick over cap, deferred to next tick",
            extra={"due_count": len(due_jobs), "cap": _MAX_DUE_PER_TICK},
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


async def _maybe_run_life_scan() -> None:
    global _LAST_LIFE_SCAN
    now = utc_now()
    if now.timestamp() - _LAST_LIFE_SCAN < _LIFE_SCAN_INTERVAL_SECONDS:
        return
    _LAST_LIFE_SCAN = now.timestamp()
    async with session_scope() as db:
        active_users = await list_user_ids(db, active_only=True)
        completed = await complete_user_ids(db, active_users)
        users = await due_review_user_ids(db, completed, now)
    for user_id in sorted(users):
        if is_user_in_maintenance(user_id):
            continue
        _spawn_scope_task("life_state", MemoryScope(user_id, COMPANION_PRESET_ID), partial(consider_life, user_id))


async def _maybe_run_outbox_gc(now: datetime) -> None:
    """定期执行 WS Outbox 历史事件与过期内部陪伴事件的物理清理。"""
    global _LAST_OUTBOX_GC_SCAN
    if now.timestamp() - _LAST_OUTBOX_GC_SCAN < _OUTBOX_GC_INTERVAL_SECONDS:
        return
    _LAST_OUTBOX_GC_SCAN = now.timestamp()
    await run_outbox_gc()


async def _review_scope_memories(scope: MemoryScope) -> bool:
    """审阅一个作用域的记忆；没有可用 LLM 配置时只记录并跳过（返回 False），不算失败。"""
    async with session_scope() as db:
        if not await user_is_active(db, scope.user_id):
            return False
        llm_config = await resolve_user_llm_config(db, scope.user_id, AiTaskKey.memory_review)
    if not llm_config.is_configured:
        logger.info("memory_review: skipped, missing llm config", extra={"user_id": scope.user_id})
        return False
    await review_memories(scope, llm_config=llm_config)
    return True


async def _maybe_run_memory_review(now: datetime) -> None:
    """按域发现待审材料并独立提交；在途域不重复提交，不阻塞其他用户的后续扫描。"""
    global _LAST_MEMORY_REVIEW_SCAN
    if now.timestamp() - _LAST_MEMORY_REVIEW_SCAN < _MEMORY_REVIEW_SCAN_INTERVAL_SECONDS:
        return
    _LAST_MEMORY_REVIEW_SCAN = now.timestamp()

    async with session_scope() as db:
        scopes = await list_memory_review_scopes(db)
    for scope in scopes:
        if is_user_in_maintenance(scope.user_id):
            continue
        if now.timestamp() - _LAST_MEMORY_REVIEW.get(scope, 0.0) < SETTINGS.memory_review_interval_seconds:
            continue
        if memory_review_backed_off(scope):
            continue
        _spawn_scope_task("memory_review", scope, partial(_run_memory_review, scope))


async def _run_memory_review(scope: MemoryScope) -> None:
    if await _review_scope_memories(scope):
        _LAST_MEMORY_REVIEW[scope] = utc_now().timestamp()


async def _maybe_run_autonomous_activity(now: datetime) -> None:
    global _LAST_NIGHTLY_SCAN
    if now.timestamp() - _LAST_NIGHTLY_SCAN < SETTINGS.nightly_scan_interval_seconds:
        return
    _LAST_NIGHTLY_SCAN = now.timestamp()
    eligible: list[tuple[MemoryScope, date]] = []
    async with session_scope() as db:
        active_users = await list_user_ids(db, active_only=True)
        eligible_users = await companion_conversation_users(db, active_users, COMPANION_PRESET_ID)
        eligible_users.update(await complete_user_ids(db, active_users))
        rows = await timezone_settings(db, eligible_users)
        if not rows:
            return
        unfinished = await unfinished_logs(db, [user_id for user_id, _ in rows])
        for user_id, timezone_name in rows:
            if is_user_in_maintenance(user_id):
                continue
            zone = parse_timezone(timezone_name)
            if zone is None:
                continue
            scope = MemoryScope(user_id, COMPANION_PRESET_ID)
            local_now = now.astimezone(zone)
            latest_date = local_now.date() - timedelta(days=1)
            await close_expired_logs(
                db,
                [log.id for log in unfinished.get(user_id, ()) if log.target_date < latest_date],
            )
            if not in_nightly_window(now, zone):
                continue
            if _LAST_NIGHTLY_RUN.get(scope) == latest_date:
                continue
            eligible.append((scope, latest_date))
        await db.commit()
    for scope, target in eligible:
        _spawn_scope_task("nightly_activity", scope, partial(_run_nightly_activity, scope, target))


async def _run_nightly_activity(scope: MemoryScope, target: date) -> None:
    async with session_scope() as db:
        if not await user_is_active(db, scope.user_id):
            return
        zone = parse_timezone(await get_user_setting(db, scope.user_id, "timezone"))
    if zone is None:
        return
    # 扫描与实际启动之间可能跨过窗口或本地午夜。
    now = utc_now()
    if target != now.astimezone(zone).date() - timedelta(days=1) or not in_nightly_window(now, zone):
        return
    if await run_nightly_pipeline(scope, target) is True:
        _LAST_NIGHTLY_RUN[scope] = target
