import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    MEMORY_REVIEW_INTERVAL_SECONDS,
    NIGHTLY_SCAN_INTERVAL_SECONDS,
    NIGHTLY_WINDOW_END_HOUR,
    NIGHTLY_WINDOW_START_HOUR,
    BackgroundTask,
    TaskBag,
    begin_local_scope,
    begin_user_request,
    end_user_request,
    get_logger,
    is_user_in_maintenance,
    session_scope,
    track_user_task,
    utc_now,
)
from modules.auth import User
from modules.companion import companion_cron_source_key
from modules.conversation import Conversation
from modules.scheduler import CronJob, NightlyActivityLog
from modules.settings import UserSetting
from sqlalchemy import DateTime, bindparam, delete, or_, select, text, tuple_
from sqlalchemy.engine import Row

from services.application.automation import execute_standard_turn
from services.application.moments import maybe_run_moment_impulse
from services.application.nightly import run_nightly_pipeline
from services.contracts import MemoryScope
from services.domains.automation import STANDARD_CRON_KIND, compute_next_run_at
from services.domains.companion import (
    enqueue_companion_intent,
    get_disturbance_tier,
    get_personality_tags,
    get_user_proactive_record,
    is_still,
    list_companion_intents,
    note_outreach_throttle,
    queue_companion_intent,
)
from services.domains.memory import review_memories
from services.infrastructure.desktop import MANAGER
from services.infrastructure.event_store import run_outbox_gc

logger = get_logger(__name__)

_BG = TaskBag("scheduler.cron")

# 每个慢扫描的在飞 task：LLM 流水线可能比扫描间隔跑得更久，而 per-user 去重标记只在成功后才写——不挡住重入会让同一用户的流水线并行跑两遍。
_SCANS: dict[str, asyncio.Task] = {}

SCHEDULER_INTERVAL_SECONDS = 60

# per-user 最近一次记忆审核运行时间戳：进程本地——匹配 ARCH §5 单实例语义（多 replica 会分裂状态）。
_LAST_MEMORY_REVIEW: dict[MemoryScope, float] = {}

# per-user 最近一次成功的 nightly pipeline 运行的本地日期字符串。
_LAST_NIGHTLY_RUN: dict[MemoryScope, str] = {}

# recall-pool 扫描本身的外层节流：扫描便宜（部分索引），但没用户符合时每分钟跑一次没意义。10 min 让发现延迟可控，由 per-user 6h 节流把重 LLM 调用频率压住。
_LAST_MEMORY_REVIEW_SCAN: float = 0.0
_MEMORY_REVIEW_SCAN_INTERVAL_SECONDS: int = 600

# nightly activity 扫描的外层节流。
_LAST_NIGHTLY_SCAN: float = 0.0

# outbox gc 扫描的外层节流（每 15 分钟运行一次）。
_LAST_OUTBOX_GC_SCAN: float = 0.0
_OUTBOX_GC_INTERVAL_SECONDS: int = 900

_IGNORED_OUTREACH_MIN_IGNORED_SECONDS = 3600  # 用户持续不理伙伴 1 小时后才有资格触发
_IGNORED_OUTREACH_MIN_SPACING_SECONDS = 3600  # 两次触发之间的最小间距——沉默结束意图后的再触发安全网


def invalidate_user_scheduler_state(user_id: int) -> None:
    """覆盖恢复后丢弃从旧数据计算出的调度节流镜像。"""
    for state in (_LAST_MEMORY_REVIEW, _LAST_NIGHTLY_RUN):
        for scope in list(state):
            if scope.user_id == user_id:
                state.pop(scope, None)


# 每个 tick 处理的到期 job 硬上限——限制批量 CAS 的语句大小和单 tick 工作量，避免长时间停摆后的回追（例如 60 分钟 ``* * * * *`` 调度，第一 tick 有 3600 个到期）。超出上限的 job 保留原 next_run_at，下一 tick 再触发。
_MAX_DUE_PER_TICK = 200

_SCHEDULER = BackgroundTask("scheduler.cron_loop")


async def drain() -> None:
    """取消并 await 所有后台 task，容忍 CancelledError；由 main.py lifespan 关停时调用，避免 SIGTERM 在 db.commit() 中途被 engine 释放。"""
    await _BG.drain()


def _log_task_error(name: str, task: asyncio.Task) -> None:
    """旁路 task 失败落日志不冒泡——_tick 的异常会杀死 BackgroundTask 曝光派发路径 bug，kickoff / 扫描失败不该连坐停掉定时派发。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("cron: background task failed", exc_info=exc, extra={"task": name})


def _spawn_scan(name: str, factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """把慢扫描移出 tick 关键路径——内部 await 多阶段 LLM 流水线，inline 会让本 tick 的 CAS 排在几分钟模型调用之后；同名扫描仍在飞则跳过本轮。"""
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


async def _select_due_jobs() -> list[Row]:
    """读取到期 job；未过期的窗口型问候只在用户在线时入选，避免离线等待项堵住全局批次。"""
    now = utc_now()
    online_user_ids = MANAGER.local_user_ids()
    expiring_scope = or_(
        CronJob.expires_at.is_(None),
        CronJob.expires_at <= now,
    )
    if online_user_ids:
        expiring_scope = or_(expiring_scope, CronJob.user_id.in_(online_user_ids))
    async with session_scope() as db:
        return (
            await db.execute(
                select(
                    CronJob.id,
                    CronJob.user_id,
                    CronJob.name,
                    CronJob.schedule,
                    CronJob.next_run_at,
                    CronJob.prompt,
                    CronJob.one_shot,
                    CronJob.kind,
                    CronJob.conversation_id,
                    CronJob.expires_at,
                )
                .where(
                    CronJob.is_paused.is_(False),
                    CronJob.next_run_at.is_not(None),
                    CronJob.next_run_at <= now,
                    expiring_scope,
                )
                .order_by(CronJob.next_run_at, CronJob.id)
                .limit(_MAX_DUE_PER_TICK + 1),
            )
        ).all()


async def _bulk_cas_advance(
    due_jobs: list[Row],
    now: datetime,
) -> dict[int, dict[str, Any]]:
    """批量 CAS 推进每个到期 job 的 next_run_at：CAS 谓词 (id, next_run_at, schedule) 防止 update_job 在 tick 中途推进 next_run_at（不匹配的行静默落败被丢弃）；用行值 IN 让 recurring UPDATE / one-shot DELETE 各一次语句搞定（PG 的 UPDATE/DELETE ... RETURNING），避免最多 200 次串行往返。返回 {job_id: {user_id, is_paused, payload}} 给 CAS 胜者，RETURNING 里没出现的视为落败丢弃。
    db.commit() 必须显式：utils.session_scope 只 auto-close 不 commit；不显式 commit 时 db.close() 结束未提交事务，SQLAlchemy 在连接归还时丢弃 UPDATE。"""
    if not due_jobs:
        return {}

    winners: dict[int, dict[str, Any]] = {}
    new_runs: dict[int, datetime | None] = {}

    for job in due_jobs:
        if job.one_shot:
            # 一次性 job 触发后删除——无需计算下次运行。
            new_runs[job.id] = None
            winners[job.id] = {
                "user_id": job.user_id,
                "is_paused": False,
                "kind": job.kind,
                "payload": {
                    "prompt": job.prompt,
                    "name": job.name,
                    "conversation_id": job.conversation_id,
                },
            }
        else:
            next_run = compute_next_run_at(job.schedule, now)
            new_runs[job.id] = next_run
            winners[job.id] = {
                "user_id": job.user_id,
                "is_paused": next_run is None,
                "kind": job.kind,
                "payload": {
                    "prompt": job.prompt,
                    "name": job.name,
                    "conversation_id": job.conversation_id,
                },
            }

    won: set[int] = set()

    async with session_scope() as db:
        await db.execute(
            select(User.id)
            .where(User.id.in_(sorted({job.user_id for job in due_jobs})))
            .order_by(User.id)
            .with_for_update(),
        )
        # 内容或轨别修改未必改变调度游标；持有用户锁后核对快照，防止撤销的旧意图再次交接。
        current_jobs: dict[int, CronJob] = {
            job.id: job
            for job in (await db.execute(select(CronJob).where(CronJob.id.in_([job.id for job in due_jobs])))).scalars()
        }
        due_jobs = [
            job
            for job in due_jobs
            if (current := current_jobs.get(job.id)) is not None
            and not current.is_paused
            and all(getattr(current, key) == value for key, value in job._mapping.items())
        ]
        recurring = [job for job in due_jobs if not job.one_shot]
        one_shots = [job for job in due_jobs if job.one_shot]
        if recurring:
            case_clauses = " ".join(f"WHEN :id_{i} THEN :next_{i}" for i in range(len(recurring)))
            stmt = text(
                f"UPDATE cron_jobs SET "
                f"next_run_at = CASE id {case_clauses} END, "
                f"is_paused = (CASE id {case_clauses} END) IS NULL "
                f"WHERE (id, next_run_at, schedule) IN :match "
                f"RETURNING id",
            ).bindparams(
                bindparam("match", expanding=True),
                *(bindparam(f"next_{i}", type_=DateTime(timezone=True)) for i in range(len(recurring))),
            )
            params: dict[str, Any] = {
                "match": [(j.id, j.next_run_at, j.schedule) for j in recurring],
            }
            for i, j in enumerate(recurring):
                params[f"id_{i}"] = j.id
                params[f"next_{i}"] = new_runs[j.id]
            res = await db.execute(stmt, params)
            won.update(r[0] for r in res.all())
        if one_shots:
            # 触发后删除一次性 job，通过 next_run_at 谓词防止并发双重触发
            stmt = (
                delete(CronJob)
                .where(
                    tuple_(CronJob.id, CronJob.next_run_at).in_(
                        bindparam("match", expanding=True),
                    ),
                )
                .returning(CronJob.id)
            )
            res = await db.execute(
                stmt,
                {"match": [(j.id, j.next_run_at) for j in one_shots]},
            )
            won.update(r[0] for r in res.all())
        for job in sorted(due_jobs, key=lambda row: (row.user_id, row.id)):
            if job.id in won and job.kind != STANDARD_CRON_KIND and not winners[job.id]["is_paused"]:
                await enqueue_companion_intent(
                    db,
                    job.user_id,
                    job.prompt,
                    source_key=companion_cron_source_key(job.id),
                    expires_at=job.expires_at,
                )
        await db.commit()

    return {job.id: winners[job.id] for job in due_jobs if job.id in won}


async def _advance_due_jobs(due_jobs: list[Row], now: datetime) -> None:
    """批量推进调度游标，并按任务轨别启动伴侣无头回合或独立任务回合。"""
    expired_ids = [job.id for job in due_jobs if job.expires_at is not None and job.expires_at <= now]
    if expired_ids:
        async with session_scope() as db:
            await db.execute(delete(CronJob).where(CronJob.id.in_(expired_ids), CronJob.expires_at <= now))
            await db.commit()

    online_users = set(MANAGER.local_user_ids())
    deferred_users = {
        int(job.user_id)
        for job in due_jobs
        if job.id not in expired_ids
        and job.kind != STANDARD_CRON_KIND
        and job.expires_at is not None
        and int(job.user_id) in online_users
    }
    still_results = await asyncio.gather(
        *(is_still(user_id) for user_id in deferred_users),
        return_exceptions=True,
    )
    still_by_user = {
        user_id: (result if isinstance(result, bool) else True)
        for user_id, result in zip(deferred_users, still_results, strict=True)
    }
    deliverable = [
        job
        for job in due_jobs
        if job.id not in expired_ids
        and (
            job.kind == STANDARD_CRON_KIND
            or job.expires_at is None
            or (int(job.user_id) in online_users and not still_by_user.get(int(job.user_id), True))
        )
    ]
    # 带 expires_at 的夜间主动问候在用户离线或静止档时保持 due；普通 special cron 仍按原周期语义推进。
    if not deliverable:
        return
    winners = await _bulk_cas_advance(deliverable, now)
    for job_id, meta in winners.items():
        if meta.get("is_paused"):
            continue
        if meta.get("kind") == STANDARD_CRON_KIND:
            task = asyncio.create_task(execute_standard_turn(meta["user_id"], job_id, meta["payload"]))
        else:
            task = asyncio.create_task(queue_companion_intent(meta["user_id"]))
        _BG.add(task, on_error=partial(_log_task_error, f"kick:{job_id}"))
        track_user_task(int(meta["user_id"]), task)


async def _queue_ignored_outreach(user_id: int, prompt: str) -> None:
    async with session_scope() as db:
        await enqueue_companion_intent(db, user_id, prompt, source_key="checkin:ignored")
        await db.commit()
    await queue_companion_intent(user_id)


async def _scan_companion_waits() -> None:
    for user_id in MANAGER.local_user_ids():
        if not await begin_user_request(user_id):
            continue
        try:
            await queue_companion_intent(user_id)
        finally:
            await end_user_request(user_id)


async def _maybe_run_ignored_outreach(now: datetime) -> None:
    """常规档下用户持续不与伙伴互动（≥1h）且无进行中外联节奏时，为粘人性格注入轻量问候 turn。

    固定 1h 间距限制无新互动时的低频问候候选
    （否则 LLM 每次都跳过时每 tick 都满足触发条件）。静止档不触发（一切主动推理断源），
    自主档不需要（完整主动能力已开放，由定时任务与等待意图承载）。
    """
    cur_time = time.monotonic()
    online_uids = MANAGER.local_user_ids()
    for uid in online_uids:
        if is_user_in_maintenance(uid):
            continue
        if await get_disturbance_tier(uid) != "normal":
            continue
        rec = get_user_proactive_record(uid)
        # 0 = 进程启动以来用户还没互动过——没有「被冷落」的基准，跳过。
        if rec.last_user_contact_ts == 0.0:
            continue
        ignored = cur_time - rec.last_user_contact_ts
        if ignored < _IGNORED_OUTREACH_MIN_IGNORED_SECONDS:
            continue
        if cur_time - rec.last_outreach_ts < _IGNORED_OUTREACH_MIN_SPACING_SECONDS:
            continue
        # 性格标签查询放在所有廉价条件之后——避免无意义地读取等待意图与性格。
        async with session_scope() as db:
            if any(
                intent.status in {"waiting", "queued", "running"} for intent in await list_companion_intents(db, uid)
            ):
                continue
            tags = await get_personality_tags(db, uid)
        if "粘人" not in tags:
            continue

        ignored_minutes = round(ignored / 60)
        prompt = json.dumps(
            {
                "kind": "low_frequency_check_in",
                "minutes_since_last_user_interaction": ignored_minutes,
                "relevant_personality_tag": "粘人",
                "intent": (
                    "把性格标签只作为表达风格参考；空闲时长不代表忽视、情绪或关系变化。"
                    "默认保持安静；只有此刻有自然且不打扰的理由时，才说一句 10–30 字的轻量关心。"
                    "不要提等待时长、责怪用户、索取回应或施加关系压力。"
                ),
            },
            ensure_ascii=False,
        )
        if not await begin_user_request(uid):
            continue
        try:
            note_outreach_throttle(uid)
            await _queue_ignored_outreach(uid, prompt)
        finally:
            await end_user_request(uid)
        logger.info(
            "cron: ignored-outreach turn requested",
            extra={"user_id": uid, "ignored_minutes": ignored_minutes},
        )


async def _maybe_run_moment_impulse() -> None:
    """白天自主片刻：对在线用户低频咨询精灵是否发一条片刻；节流与门控在 maybe_run_moment_impulse 内。"""
    for uid in MANAGER.local_user_ids():
        await maybe_run_moment_impulse(uid)


async def _tick() -> None:
    """为到期 job CAS 推进 next_run_at；special 持久化等待意图，standard 直接启动独立任务回合。"""
    now = utc_now()
    # 慢扫描与 cron-job 派发独立——不能用 ``if not due_jobs`` gate，否则没有 cron job 的安装永远不会触发记忆维护/GC。
    _spawn_scan("memory_review", lambda: _maybe_run_memory_review(now))
    _spawn_scan("nightly_activity", lambda: _maybe_run_autonomous_activity(now))
    _spawn_scan("outbox_gc", lambda: _maybe_run_outbox_gc(now))
    _spawn_scan("ignored_outreach", lambda: _maybe_run_ignored_outreach(now))
    _spawn_scan("companion_waits", _scan_companion_waits)
    _spawn_scan("moment_impulse", _maybe_run_moment_impulse)
    due_jobs = await _select_due_jobs()
    if len(due_jobs) > _MAX_DUE_PER_TICK:
        logger.warning(
            "cron: tick over cap, deferred to next tick",
            extra={"due_count": len(due_jobs), "cap": _MAX_DUE_PER_TICK},
        )
    due_jobs = due_jobs[:_MAX_DUE_PER_TICK]
    if not due_jobs:
        return
    leases: dict[int, bool] = {}
    for job in due_jobs:
        uid = int(job.user_id)
        if uid not in leases:
            leases[uid] = await begin_user_request(uid)
    try:
        await _advance_due_jobs([job for job in due_jobs if leases[int(job.user_id)]], now)
    finally:
        await asyncio.gather(*(end_user_request(uid) for uid, acquired in leases.items() if acquired))


async def _maybe_run_outbox_gc(now: datetime) -> None:
    """定期执行 WS Outbox 历史事件与过期内部陪伴事件的物理清理。"""
    global _LAST_OUTBOX_GC_SCAN
    if now.timestamp() - _LAST_OUTBOX_GC_SCAN < _OUTBOX_GC_INTERVAL_SECONDS:
        return
    _LAST_OUTBOX_GC_SCAN = now.timestamp()
    await run_outbox_gc()


async def _maybe_run_memory_review(now: datetime) -> None:
    """为有记忆或待审核消息的预设执行证据维护——外层按 _MEMORY_REVIEW_SCAN_INTERVAL_SECONDS 节流，per-user 按 MEMORY_REVIEW_INTERVAL_SECONDS 节流，并发通过 gather 单 tick 只付最大 LLM 延迟。"""
    global _LAST_MEMORY_REVIEW_SCAN
    if now.timestamp() - _LAST_MEMORY_REVIEW_SCAN < _MEMORY_REVIEW_SCAN_INTERVAL_SECONDS:
        return
    _LAST_MEMORY_REVIEW_SCAN = now.timestamp()

    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT user_id, system_preset_id FROM memories WHERE context LIKE 'recall:%' AND status != 'forgotten' "
                    "UNION SELECT c.user_id, c.system_preset_id FROM conversations c JOIN messages m ON m.conversation_id = c.id "
                    "WHERE NOT c.is_automation AND m.id > GREATEST(c.context_after_message_id, c.memory_reviewed_message_id) "
                    "AND m.role IN ('user', 'assistant') AND m.subtype IS NULL",
                ),
            )
        ).all()
    eligible: list[MemoryScope] = []
    for uid_raw, preset in rows:
        uid = int(uid_raw)
        scope = MemoryScope(uid, preset)
        if is_user_in_maintenance(uid):
            continue
        if now.timestamp() - _LAST_MEMORY_REVIEW.get(scope, 0.0) < MEMORY_REVIEW_INTERVAL_SECONDS:
            continue
        eligible.append(scope)
    if not eligible:
        return

    # 预设级节流只在维护成功之后才生效——LLM 失败不该把用户锁在后续尝试之外。
    tasks = [asyncio.create_task(review_memories(scope), name=f"scheduler.memory.{scope}") for scope in eligible]
    for scope, task in zip(eligible, tasks, strict=True):
        track_user_task(scope.user_id, task)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for scope, result in zip(eligible, results, strict=True):
        uid = scope.user_id
        if isinstance(result, Exception):
            # 不在 except 块里——必须显式传异常，否则 exc_info 为空，traceback 丢失。
            logger.error(
                "memory_review: tick failed",
                exc_info=result,
                extra={"user_id": uid},
            )
            continue
        _LAST_MEMORY_REVIEW[scope] = now.timestamp()


async def _maybe_run_autonomous_activity(now: datetime) -> None:
    global _LAST_NIGHTLY_SCAN
    if now.timestamp() - _LAST_NIGHTLY_SCAN < NIGHTLY_SCAN_INTERVAL_SECONDS:
        return
    _LAST_NIGHTLY_SCAN = now.timestamp()
    eligible: list[tuple[MemoryScope, datetime, date]] = []
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(Conversation.user_id, Conversation.system_preset_id, UserSetting.setting_value)
                .join(User, User.id == Conversation.user_id)
                .join(UserSetting, UserSetting.user_id == User.id)
                .where(
                    User.nightly_activity_enabled.is_(True),
                    Conversation.is_automation.is_(False),
                    UserSetting.setting_key == "timezone",
                )
                .distinct(),
            )
        ).all()
        logs = list(
            (
                await db.scalars(
                    select(NightlyActivityLog)
                    .where(NightlyActivityLog.status.in_(("running", "failed")))
                    .order_by(NightlyActivityLog.target_date.desc()),
                )
            ).all(),
        )
        for user_id, preset, timezone_name in rows:
            if is_user_in_maintenance(user_id):
                continue
            scope = MemoryScope(user_id, preset)
            try:
                timezone = ZoneInfo(timezone_name)
                local_now = now.astimezone(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                continue
            recover = None
            for log in logs:
                if log.user_id != user_id or log.system_preset_id != preset:
                    continue
                if log.target_date < local_now.date() - timedelta(days=1) or recover is not None:
                    log.status = "completed_with_errors"
                    log.summary = "已超过恢复窗口或有更新日期优先恢复，未确认动作不再重放"
                else:
                    recover = log.target_date
            target = recover or local_now.date() - timedelta(days=1)
            if recover is None and not NIGHTLY_WINDOW_START_HOUR <= local_now.hour < NIGHTLY_WINDOW_END_HOUR:
                continue
            if _LAST_NIGHTLY_RUN.get(scope) == target.isoformat():
                continue
            reference = datetime.combine(target, datetime.min.time(), timezone).replace(hour=12).astimezone(UTC)
            eligible.append((scope, reference, target))
        await db.commit()
    tasks = [
        asyncio.create_task(
            run_nightly_pipeline(scope, reference, target_date=target),
            name=f"scheduler.nightly.{scope}.{target}",
        )
        for scope, reference, target in eligible
    ]
    for (scope, _, _), task in zip(eligible, tasks, strict=True):
        track_user_task(scope.user_id, task)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for (scope, _, target), result in zip(eligible, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("nightly_activity: tick failed", exc_info=result, extra={"scope": str(scope)})
        elif result is True:
            _LAST_NIGHTLY_RUN[scope] = target.isoformat()


async def scheduler_loop() -> None:
    """以 SCHEDULER_INTERVAL_SECONDS 为周期的 cron tick 循环：单 tick 粒度——不支持分钟以下调度。按 deadline 对齐而非 tick 结束后固定 sleep——后者的实际周期是 60s + tick 耗时，误差逐轮累积；落后超过一整周期时丢弃错过的槽位，避免停摆恢复后连打。_tick() 未捕获的异常会冒泡导致 BackgroundTask 死亡，运维侧曝光度高（Task exited with error）——这是故意的：持久 bug 不该每 60 秒静默刷日志，而应显式崩溃以便修复；派发出去的自主 turn 与扫描各自在独立 task 里失败并落日志，不会终止循环。"""
    logger.info("Starting background cron scheduler loop.")
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    while True:
        begin_local_scope()
        await _tick()
        next_at += SCHEDULER_INTERVAL_SECONDS
        now = loop.time()
        if next_at <= now:
            next_at = now + SCHEDULER_INTERVAL_SECONDS
        await asyncio.sleep(next_at - now)


def start_scheduler() -> None:
    _SCHEDULER.start(scheduler_loop())


async def stop_scheduler() -> None:
    """取消 scheduler task 并等待其退出；await 防止晚到的 tick 在 dispatcher 已拆除后还触发自主 turn（那些 turn 会失去 emitter）。"""
    await _SCHEDULER.stop()
