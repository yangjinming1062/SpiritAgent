import asyncio
import contextlib
import random
import secrets
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import asyncpg
from components import BackgroundTask, begin_local_scope, get_logger, safe_json_loads, session_scope, utc_now
from fastapi import WebSocket
from modules.ws import CRON_TURN_EVENT, WSEvent
from sqlalchemy import select, update

from .jsonrpc import JsonRpcDispatcher

logger = get_logger(__name__)

# 每次批量认领的上限
WS_EVENT_CLAIM_BATCH_SIZE = 100

# 最大重试次数（超过即转入 FAILED 死信状态）
MAX_OUTBOX_RETRIES = 5

# 僵尸锁恢复超时（秒）
STALE_LOCK_TIMEOUT_SECONDS = 60

# 进程唯一 Worker ID（用于原子锁追踪）
WORKER_ID = f"worker-{secrets.token_hex(4)}"

# 按 user 持有强引用，避免 spawn 的 cron turn 在运行到一半时被 GC（CPython bpo-46662）。
_cron_turn_tasks: dict[int, set[asyncio.Task]] = {}

CronTurnHandler = Callable[[int, dict], Awaitable[None]]
_cron_turn_handler: CronTurnHandler | None = None


def register_cron_turn_handler(handler: CronTurnHandler) -> None:
    global _cron_turn_handler
    _cron_turn_handler = handler


def get_cron_turn_handler() -> CronTurnHandler | None:
    """返回已注册的 cron_turn_handler；若未注册则尝试从 services.gateway.cron_turns 自愈加载。"""
    global _cron_turn_handler
    if _cron_turn_handler is None:
        try:
            from services.gateway.cron_turns import execute_cron_turn

            _cron_turn_handler = execute_cron_turn
        except Exception:
            pass
    return _cron_turn_handler


class _WakeupState:
    def __init__(self) -> None:
        self.version: int = 0
        self.event: asyncio.Event = asyncio.Event()

    def notify(self) -> None:
        self.version += 1
        self.event.set()


# 全局唤醒状态，用于即时冲刷（Instant Drain）与 NOTIFY 触发
_WAKEUP_STATE: _WakeupState = _WakeupState()

_WS_EVENT_LOOP = BackgroundTask("ws.event_loop")


def notify_ws_event_loop() -> None:
    """唤醒 WS Outbox 派发循环立即执行一轮事件捞取与冲刷。"""
    _WAKEUP_STATE.notify()


def _discard_cron_task(user_id: int, task: asyncio.Task) -> None:
    user_tasks = _cron_turn_tasks.get(user_id)
    if user_tasks is not None:
        user_tasks.discard(task)
        if not user_tasks:
            _cron_turn_tasks.pop(user_id, None)


def _log_cron_task_failure(task: asyncio.Task) -> None:
    """cron handler 抛错时落地日志；与 _discard_cron_task 拆开避免互相清理顺序耦合。"""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.exception("cron turn handler raised", exc_info=exc)


def cancel_user_cron_turns(user_id: int) -> int:
    user_tasks = _cron_turn_tasks.pop(user_id, None)
    if not user_tasks:
        return 0
    cancelled = 0
    for t in user_tasks:
        if not t.done():
            t.cancel()
            cancelled += 1
    return cancelled


async def interrupt_user_cron_turns(user_id: int) -> int:
    user_tasks = list(_cron_turn_tasks.pop(user_id, ()))
    pending = [task for task in user_tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return len(pending)


async def drain_cron_turns() -> None:
    """展平 per-user 的 _cron_turn_tasks → 取消并 await 所有 task。"""
    pending: list[asyncio.Task] = []
    for user_tasks in list(_cron_turn_tasks.values()):
        pending.extend(user_tasks)
    if not pending:
        return
    for t in pending:
        if not t.done():
            t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, WebSocket] = {}
        self._login_record_ids: dict[int, int] = {}
        self._dispatchers: dict[int, JsonRpcDispatcher] = {}
        # 同会话 per-runtime map（同 _dispatchers 一并注册；存引用，runtime 挂载/卸载由 handlers.py 直接 mutate 同一 dict）。
        self._runtime_sessions: dict[int, dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket, user_id: int, login_record_id: int) -> None:
        """accept 并注册；同一用户已存在的 socket 也在此处关闭，把单设备登录不变量集中在一处，不与调用方分散。"""
        prior = self.active_connections.get(user_id)
        await websocket.accept()
        self.active_connections[user_id] = websocket
        self._login_record_ids[user_id] = login_record_id
        logger.info("User connected", extra={"user_id": user_id})
        if prior is not None:
            with contextlib.suppress(Exception):
                await prior.close(code=1000)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        if self.active_connections.get(user_id) is websocket:
            del self.active_connections[user_id]
            self._login_record_ids.pop(user_id, None)
            logger.info("User socket disconnected", extra={"user_id": user_id})

    def get_login_record_id(self, user_id: int) -> int | None:
        return self._login_record_ids.get(user_id)

    def register_dispatcher(self, user_id: int, dispatcher: JsonRpcDispatcher) -> None:
        self._dispatchers[user_id] = dispatcher
        dispatcher.start_writer()
        logger.info("User dispatcher registered", extra={"user_id": user_id})
        notify_ws_event_loop()

    def register_runtime_sessions(self, user_id: int, runtime_sessions: dict[str, Any]) -> None:
        """注册同用户 runtime_sessions dict 引用——handlers.py 持续 mutate 此 dict，MANAGER 仅持有引用以供 REST 等非 WS 路径按 user_id 查表。"""
        self._runtime_sessions[user_id] = runtime_sessions

    def get_runtime_sessions(self, user_id: int) -> dict[str, Any] | None:
        return self._runtime_sessions.get(user_id)

    async def aunregister_dispatcher(self, user_id: int) -> None:
        dispatcher = self._dispatchers.pop(user_id, None)
        self._runtime_sessions.pop(user_id, None)
        if dispatcher is not None:
            await dispatcher.stop_writer()
            delivered = dispatcher.drain_delivered_ids()
            if delivered:
                with contextlib.suppress(Exception):
                    await _mark_events_delivered(delivered)
        logger.info("User dispatcher async unregistered", extra={"user_id": user_id})

    def get_dispatcher(self, user_id: int) -> JsonRpcDispatcher | None:
        return self._dispatchers.get(user_id)

    def is_connected(self, user_id: int) -> bool:
        return user_id in self.active_connections

    def is_available(self, user_id: int) -> bool:
        return user_id in self.active_connections or user_id in self._dispatchers

    def local_user_ids(self) -> list[int]:
        """已注册 dispatcher 的 user_id 快照——outbox 轮询循环用它限定认领范围。"""
        return list(self._dispatchers.keys())


MANAGER = ConnectionManager()


def compute_backoff(retry_count: int) -> timedelta:
    """指数退避叠加等比抖动（Equal Jitter），避免多副本同步唤醒重投导致尖峰。"""
    base = min(60, 2**retry_count)
    half = base / 2
    return timedelta(seconds=random.uniform(half, base))


async def _recover_stale_locks() -> None:
    """恢复超过 STALE_LOCK_TIMEOUT_SECONDS 仍处于 PROCESSING 的僵尸锁行。"""
    cutoff = utc_now() - timedelta(seconds=STALE_LOCK_TIMEOUT_SECONDS)
    async with session_scope() as db:
        await db.execute(
            update(WSEvent)
            .where(WSEvent.status == "PROCESSING", WSEvent.locked_at < cutoff)
            .values(status="PENDING", locked_by=None, locked_at=None),
        )
        await db.commit()


async def _claim_pending_events(
    local_user_ids: list[int],
    limit: int = WS_EVENT_CLAIM_BATCH_SIZE,
) -> list[tuple[int, str, str, int, int]]:
    """以原子锁认领待投递事件。"""
    now = utc_now()
    claimed: list[tuple[int, str, str, int, int]] = []
    conditions = [
        WSEvent.user_id.in_(local_user_ids),
        WSEvent.status == "PENDING",
        WSEvent.next_retry_at <= now,
    ]
    if get_cron_turn_handler() is None:
        conditions.append(WSEvent.event_type != CRON_TURN_EVENT)

    async with session_scope() as db:
        subq = (
            select(WSEvent.id)
            .where(*conditions)
            .order_by(WSEvent.created_at, WSEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        rows = (
            await db.execute(
                update(WSEvent)
                .where(WSEvent.id.in_(subq))
                .values(status="PROCESSING", locked_by=WORKER_ID, locked_at=now)
                .returning(
                    WSEvent.id,
                    WSEvent.event_type,
                    WSEvent.payload,
                    WSEvent.user_id,
                    WSEvent.created_at,
                    WSEvent.retry_count,
                ),
            )
        ).all()
        rows.sort(key=lambda r: (r[4], r[0]))
        for r in rows:
            claimed.append((r[0], r[1], r[2], r[3], r[5]))
        await db.commit()
    return claimed


async def _mark_events_delivered(event_ids: list[int]) -> None:
    if not event_ids:
        return
    now = utc_now()
    async with session_scope() as db:
        await db.execute(
            update(WSEvent)
            .where(WSEvent.id.in_(event_ids))
            .values(status="DELIVERED", delivered_at=now, locked_by=None, error_message=None),
        )
        await db.commit()


async def _mark_event_failure(event_id: int, current_retries: int, error: str | None = None) -> None:
    now = utc_now()
    new_retry_count = current_retries + 1
    if new_retry_count >= MAX_OUTBOX_RETRIES:
        new_status = "FAILED"
        next_retry = now + timedelta(days=365)
    else:
        new_status = "PENDING"
        next_retry = now + compute_backoff(current_retries)

    async with session_scope() as db:
        await db.execute(
            update(WSEvent)
            .where(WSEvent.id == event_id)
            .values(
                status=new_status,
                retry_count=new_retry_count,
                next_retry_at=next_retry,
                locked_by=None,
                error_message=(error or "Unknown delivery error")[:500],
            ),
        )
        await db.commit()


async def _flush_gateway_delivered() -> None:
    """网关级统一将所有已由 writer 成功送达客户端的 Outbox 事件批量标记为 DELIVERED。"""
    all_delivered: list[int] = []
    for user_id in MANAGER.local_user_ids():
        d = MANAGER.get_dispatcher(user_id)
        if d is not None:
            all_delivered.extend(d.drain_delivered_ids())
    if all_delivered:
        await _mark_events_delivered(all_delivered)


async def _periodic_flusher_loop():
    try:
        while True:
            await asyncio.sleep(1.0)
            with contextlib.suppress(Exception):
                await _flush_gateway_delivered()
    except asyncio.CancelledError:
        pass


async def ws_event_loop(dsn: str):
    """基于 PostgreSQL LISTEN/NOTIFY 的 WS outbox 派发：进程持有专用的 asyncpg 连接（被 LISTEN pin 住），出错后 5s 重连以避免 PG 重启/网络抖动让派发器永久失聪。"""
    logger.info("Starting background WS event loop with PG LISTEN/NOTIFY.")
    seen_version = -1  # 初始传递 -1，使启动时第一轮立即执行排空已提交事件

    def _listener(_conn, _pid, _channel, _payload):
        notify_ws_event_loop()

    flusher_task = asyncio.create_task(_periodic_flusher_loop())
    try:
        while True:
            try:
                conn = await asyncpg.connect(dsn)
                try:
                    await conn.add_listener("ws_events_channel", _listener)
                    try:
                        while True:
                            seen_version = await _process_events(seen_version)
                    finally:
                        with contextlib.suppress(Exception):
                            await conn.remove_listener("ws_events_channel", _listener)
                finally:
                    await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error in WS event loop connection, reconnecting in 5s", extra={"error": str(e)})
                await asyncio.sleep(5)
    finally:
        flusher_task.cancel()
        with contextlib.suppress(Exception):
            await flusher_task
        with contextlib.suppress(Exception):
            await _flush_gateway_delivered()


async def _process_events(seen: int = -1) -> int:
    try:
        begin_local_scope()
        if _WAKEUP_STATE.version <= seen:
            _WAKEUP_STATE.event.clear()
            if _WAKEUP_STATE.version <= seen:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(_WAKEUP_STATE.event.wait(), timeout=60.0)

        # 关键：在从 DB 认领事件前快照当前版本号
        current_version = _WAKEUP_STATE.version

        await _recover_stale_locks()
        await _flush_gateway_delivered()

        local_user_ids = MANAGER.local_user_ids()
        if not local_user_ids:
            return current_version

        claimed = await _claim_pending_events(local_user_ids)
        if not claimed:
            return current_version

        cron_delivered_ids: list[int] = []
        for event_id, event_type, payload_str, user_id, retry_count in claimed:
            payload = safe_json_loads(payload_str)
            if payload is None:
                logger.warning("Skipping and failing unparseable WSEvent", extra={"event_id": event_id})
                await _mark_event_failure(event_id, retry_count, error="Unparseable JSON payload")
                continue

            if event_type == CRON_TURN_EVENT:
                handler = get_cron_turn_handler()
                if handler is None:
                    logger.warning("cron turn handler not registered; reverting event %s to PENDING", event_id)
                    async with session_scope() as db:
                        await db.execute(
                            update(WSEvent)
                            .where(WSEvent.id == event_id)
                            .values(
                                status="PENDING",
                                locked_by=None,
                                locked_at=None,
                                next_retry_at=utc_now() + timedelta(seconds=5),
                            ),
                        )
                        await db.commit()
                    continue
                task = asyncio.create_task(handler(user_id, payload))
                user_tasks = _cron_turn_tasks.setdefault(user_id, set())
                user_tasks.add(task)
                task.add_done_callback(lambda t, uid=user_id: _discard_cron_task(uid, t))
                task.add_done_callback(_log_cron_task_failure)
                cron_delivered_ids.append(event_id)
                continue

            try:
                dispatcher = MANAGER.get_dispatcher(user_id)
                if dispatcher is None:
                    raise RuntimeError(f"User {user_id} dispatcher not available")
                enqueued = await dispatcher.enqueue_event(event_type, payload, event_id=event_id)
                if not enqueued:
                    await _mark_event_failure(event_id, retry_count, error="Outbox writer queue full")
            except Exception as e:
                logger.error(
                    "Failed to dispatch event to user",
                    extra={"event_id": event_id, "event_type": event_type, "user_id": user_id, "error": str(e)},
                )
                await _mark_event_failure(event_id, retry_count, error=str(e))

        if cron_delivered_ids:
            await _mark_events_delivered(cron_delivered_ids)

        await _flush_gateway_delivered()
        return current_version

    except Exception as e:
        logger.error("Error processing events", extra={"error": str(e)})
        return _WAKEUP_STATE.version


def start_ws_event_loop(dsn: str, *, cron_turn_handler: CronTurnHandler | None = None) -> None:
    if cron_turn_handler is not None:
        register_cron_turn_handler(cron_turn_handler)
    _WS_EVENT_LOOP.start(ws_event_loop(dsn))


async def stop_ws_event_loop() -> None:
    """取消 WS event loop task 并等待其退出；await 可避免晚到的派发与关闭拆除竞速。"""
    await _WS_EVENT_LOOP.stop()
