import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from components import (
    JSON_RPC_VERSION,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    async_trace_span,
    get_logger,
)

from .buffer import BufferedFrame, ReplayBuffer

logger = get_logger(__name__)

OUTBOX_QUEUE_MAX = 1024


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# Handler 返回 JSON-RPC result。raise JsonRpcError 会以结构化错误回复；其他异常统一变 -32603。
Handler = Callable[[dict], Awaitable[Any]]

# 必须从给 renderer 的错误里抹掉的服务端内部痕迹（PROTOCOL「错误信封」：错误不包含凭据、数据库连接或服务端本地路径）。精选而非宽泛：文件系统路径、DSN/URL 凭据、OpenAI/httpx 异常格式、Python traceback 行。
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*Traceback \(most recent call last\):.*", re.DOTALL),
    re.compile(r"(/[A-Za-z0-9_.+-]+){2,}/[A-Za-z0-9_.-]+\.py:\d+"),
    re.compile(r"[A-Za-z]:\\[A-Za-z0-9_.\\ -]+\.py:\d+"),
    # 通用文件系统路径（/var/lib、/tmp、/etc 等）——上面的 .py 模式抓不到目录或非 Python 文件。
    re.compile(r"/(?:var|tmp|etc|home|root|opt|srv|mnt)/[A-Za-z0-9_./-]+"),
    # postgresql / psycopg / asyncpg 内嵌 user:pass 的 DSN；匹配延伸到 @ 后第一个空白字符，确保凭据后的内部主机名也一并被清掉。
    re.compile(r"postgresql(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^@\s/]+:[^@\s/]+@\S+"),
    # 上一条漏掉的非 postgres DSN：redis / mongodb / amqp / kafka。
    re.compile(r"(?:redis|mongodb|amqp|kafka)(?:\+[A-Za-z]+)?://[^@\s/]+:[^@\s/]+@\S+"),
    # Bearer / API-key header 值（Authorization / X-API-Key / openai 风格）。
    re.compile(r"(?:Bearer\s+|(?:X-)?Api-Key:\s*|(?:sk|xai|gAAAA|hsk)-)[A-Za-z0-9._\-]{12,}"),
    # OpenAI/GitHub/Slack 风格 token，以及 provider SDK 自带的若干新前缀。
    re.compile(r"(sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|xox[abp]-|xapp-[A-Za-z0-9-]{20,})[A-Za-z0-9_-]+"),
    re.compile(
        r"\b(?:[A-Za-z0-9_]+\.)*(?:OperationalError|IntegrityError|FileNotFoundError|ConnectionError|TimeoutError)\b|\bsqlalchemy\.exc\.[A-Za-z]+\b",
    ),
    # IPv4（含 RFC1918 / loopback）——OperationalError 清理对 "10.0.0.5:5432" 这种裸 host:port 片段无效，专门在此处补上。
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"),
    # IPv6：括号包裹（psycopg/asyncpg 形式 `[::1]:5432`）和裸形式（host:port 清理抓不到这两类）。
    re.compile(r"\[?[0-9a-fA-F:]+\]?:\d{2,5}"),
    re.compile(r"\b(?:fe80|fc|fd)[0-9a-fA-F:]{8,}\b"),
    # psycopg/asyncpg 错误串里出现的裸主机名
    re.compile(r"\b(?:host|server)\s+(?:at\s+)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE),
    # PostgreSQL DSN user/role 片段（如 ``for user "postgres"``）
    re.compile(r"for user\s+\"[A-Za-z0-9_.-]+\"", re.IGNORECASE),
    # SQLAlchemy / psycopg "password authentication failed" 等标签
    re.compile(r"\b(?:password authentication failed|FATAL:\s+[A-Z][A-Za-z ]+?)\b", re.IGNORECASE),
    # JWT 三段式点分 token
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


def redact_message(message: str) -> str:
    """在 -32603 消息离开网关前清掉服务端内部痕迹：完整原文通过 logger.exception 留在服务端日志，仅清洗后的标签到达 renderer。"""
    out = message
    for pat in _REDACT_PATTERNS:
        out = pat.sub("[redacted]", out)
    return out[:512]


def _redact_data(data: Any) -> Any:
    """递归清洗结构化 error data 中的字符串叶子。"""
    if isinstance(data, str):
        return redact_message(data)
    if isinstance(data, dict):
        return {k: _redact_data(v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [_redact_data(v) for v in data]
    return data


# 发送单个 JSON 帧，返回是否确认写出；连接已断开时返回 False 而不抛出。
Sender = Callable[[dict[str, Any]], Awaitable[bool]]

_HOLD_TIMEOUT_SECONDS = 10.0


class JsonRpcDispatcher:
    def __init__(self, send: Sender) -> None:
        self._send = send
        self.replay_buffer = ReplayBuffer()
        self._handlers: dict[str, Handler] = {}
        self._send_lock = asyncio.Lock()
        self._hold_events: bool = False
        self._hold_timeout_task: asyncio.Task | None = None
        self._outbox: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue(maxsize=OUTBOX_QUEUE_MAX)
        self._writer_task: asyncio.Task | None = None
        self._delivered_ids: list[int] = []
        # seq → outbox 事件 ID：任一发送路径（writer / flush / replay）送达时回收为 delivered 记账。
        self._pending_outbox_events: dict[int, int] = {}

    def set_sender(self, send: Sender) -> None:
        self._send = send

    def start_writer(self) -> None:
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._writer_loop())

    @property
    def writer_task(self) -> asyncio.Task | None:
        return self._writer_task

    async def stop_writer(self) -> None:
        if self._writer_task is not None:
            self._writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer_task
            self._writer_task = None

    def drain_delivered_ids(self) -> list[int]:
        ids = self._delivered_ids[:]
        self._delivered_ids.clear()
        return ids

    def _record_delivered(self, seq: int) -> None:
        if (event_id := self._pending_outbox_events.pop(seq, None)) is not None:
            self._delivered_ids.append(event_id)

    async def _writer_loop(self) -> None:
        try:
            while True:
                # 等 hold 释放再拉，避免和 flush_unsent / replay 抢同一帧的发送路径
                while self._hold_events:
                    await asyncio.sleep(0.05)
                seq, frame = await self._outbox.get()
                try:
                    async with self._send_lock:
                        if self._hold_events:
                            # 极小竞态：拉取瞬间又进 hold；该帧仍为 sent=False，留给 flush_unsent 发送
                            continue
                        if self.replay_buffer.is_sent(seq):
                            # flush_unsent / replay 已发，跳过物理发送但仍需记账
                            self._record_delivered(seq)
                            continue
                        if await self._send(frame):
                            self.replay_buffer.mark_sent_through(seq)
                            self._record_delivered(seq)
                        else:
                            # 不标记 sent，留给重连后的 flush_unsent；outbox 事件由僵尸锁恢复重投
                            logger.warning(
                                "outbox writer send failed, deferring to stale-lock recovery",
                                extra={"seq": seq, "event_id": self._pending_outbox_events.get(seq)},
                            )
                finally:
                    self._outbox.task_done()
        except asyncio.CancelledError:
            pass

    def _cancel_hold_timeout(self) -> None:
        task = self._hold_timeout_task
        self._hold_timeout_task = None
        # 超时任务自身触发 flush 时不能自我取消，否则 flush 在首个挂起点中断、hold 永不释放。
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def enable_hold(self) -> None:
        """激活事件 hold：push_event 只追加到缓冲，等到挂载（session.resume / session.get_main / session.create 等）时再 flush。"""
        self._hold_events = True
        self._cancel_hold_timeout()

        async def _timeout_flush() -> None:
            try:
                await asyncio.sleep(_HOLD_TIMEOUT_SECONDS)
                if self._hold_events:
                    logger.warning("Event hold timed out without mount request; forcing flush_unsent")
                    await self.flush_unsent()
            except asyncio.CancelledError:
                pass

        self._hold_timeout_task = asyncio.create_task(_timeout_flush())

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    async def handle_raw(self, data: str) -> None:
        """解析原始 WS 帧并派发：JSON 非法时返回 -32700 Parse error，解析成非对象时返回 -32600 Invalid Request；错误回复的 id 按 JSON-RPC 2.0 §5.1 为 null。"""
        try:
            msg = json.loads(data)
        except ValueError:
            await self._reply_error(None, JSONRPC_PARSE_ERROR, "Parse error: malformed JSON")
            return
        if not isinstance(msg, dict):
            await self._reply_error(None, JSONRPC_INVALID_REQUEST, "Request must be a JSON object")
            return
        await self.handle(msg)

    async def handle(self, msg: dict) -> None:
        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}

        if not isinstance(method, str):
            await self._reply_error(msg_id, JSONRPC_INVALID_REQUEST, "method must be a string")
            return

        # 通知（无 id）的成功与失败都不回复，失败只记日志。
        is_notification = msg_id is None
        handler = self._handlers.get(method)
        if handler is None:
            if is_notification:
                logger.warning("jsonrpc notification for unknown method", extra={"method": method})
                return
            await self._reply_error(msg_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")
            return

        try:
            async with async_trace_span(f"rpc.{method}", attributes={"rpc.id": msg_id}):
                result = await handler(params)
        except JsonRpcError as e:
            if is_notification:
                logger.warning("jsonrpc notification rejected", extra={"method": method, "code": e.code})
                return
            await self._reply_error(msg_id, e.code, e.message, e.data)
            return
        except Exception as e:
            # 不外泄内部细节：完整异常记在服务端日志，向 renderer 发清洗后的标签。PROTOCOL「错误信封」。
            logger.exception("jsonrpc method failed", extra={"method": method})
            if is_notification:
                return
            label = f"{type(e).__name__}: {e}"
            await self._reply_error(msg_id, JSONRPC_INTERNAL_ERROR, redact_message(label))
            return

        if is_notification:
            return
        await self._reply_result(msg_id, result)

    async def enqueue_event(
        self,
        event_type: str,
        payload: Any = None,
        session_id: str | None = None,
        event_id: int | None = None,
    ) -> bool:
        """事件帧入缓冲并交给 writer；返回 False 表示队列满或 writer 未运行时同步发送失败。"""
        params: dict[str, Any] = {"type": event_type}
        if session_id is not None:
            params["session_id"] = session_id
        if payload is not None:
            params["payload"] = payload
        seq, frame = self.replay_buffer.append({"jsonrpc": JSON_RPC_VERSION, "method": "event", "params": params})
        if event_id is not None:
            self._pending_outbox_events[seq] = event_id

        if self._hold_events:
            return True

        if self._writer_task is not None and not self._writer_task.done():
            try:
                self._outbox.put_nowait((seq, frame))
                return True
            except asyncio.QueueFull:
                logger.warning(
                    "outbox writer queue full, dropping event",
                    extra={"event_type": event_type, "seq": seq, "queue_size": self._outbox.qsize()},
                )
                self._pending_outbox_events.pop(seq, None)
                return False

        # writer 未运行（注销后仍被在途任务引用）：同步发送
        async with self._send_lock:
            if not await self._send(frame):
                return False
            self.replay_buffer.mark_sent_through(seq)
            self._record_delivered(seq)
            return True

    async def push_event(self, event_type: str, payload: Any = None, session_id: str | None = None) -> None:
        await self.enqueue_event(event_type, payload, session_id=session_id)

    async def push_error_event(self, message: str, session_id: str | None = None) -> None:
        # push_event 绕开 _reply_error，原始异常文本必须在此处显式 redact（PROTOCOL「错误信封」）。
        await self.push_event("error", {"message": redact_message(message)}, session_id=session_id)

    async def _send_frames(self, frames: list[BufferedFrame]) -> bool:
        """按序发送缓冲帧并记账；发送失败即停，未发帧不得标记 sent，否则 writer 会误记 delivered、outbox 事件失去重投。"""
        for f in frames:
            if not await self._send(f.frame):
                return False
            f.sent = True
            self._record_delivered(f.seq)
        return True

    async def _flush_locked(self, after_seq: int) -> None:
        # 外层循环覆盖发送挂起期间新入缓冲的帧。
        while unsent := self.replay_buffer.get_unsent(after_seq):
            if not await self._send_frames(unsent):
                return

    async def replay(self, last_seq: int) -> int | None:
        """在 send lock 内重放 last_seq 之后的帧、补发新帧并释放 hold，返回重放帧数。

        缓冲已不覆盖 last_seq 时返回 None 且保持 hold，由调用方改走历史同步后 flush_unsent 释放。
        """
        async with self._send_lock:
            frames = self.replay_buffer.replay_since(last_seq)
            if frames is None:
                return None
            self._cancel_hold_timeout()
            await self._send_frames(frames)
            await self._flush_locked(last_seq)
            self._hold_events = False
            return len(frames)

    async def flush_unsent(self) -> None:
        """在 send lock 内按序补发所有未发缓冲帧，然后释放 hold。"""
        self._cancel_hold_timeout()
        async with self._send_lock:
            await self._flush_locked(0)
            self._hold_events = False

    async def _reply_result(self, msg_id: Any, result: Any) -> None:
        async with self._send_lock:
            await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "result": result})

    async def _reply_error(self, msg_id: Any, code: int, message: str, data: Any = None) -> None:
        # 按规范 §5.1：错误回复的 id 为请求 id 或 null（请求不可解析时）。raise 点负责给出对用户友好的消息；此处仍跑 redact 作为兜底。
        error = {
            "code": code,
            "message": redact_message(message),
            **({"data": _redact_data(data)} if data is not None else {}),
        }
        async with self._send_lock:
            await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "error": error})
