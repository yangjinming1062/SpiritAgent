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

from .buffer import ReplayBuffer

logger = get_logger(__name__)


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# Handler 返回 JSON-RPC result。raise JsonRpcError 会以结构化错误回复；其他异常统一变 -32603。
Handler = Callable[[dict], Awaitable[Any]]

# 必须从给 renderer 的错误里抹掉的服务端内部痕迹（ARCH §11#2：-32603 严禁包含数据库账号、服务器本地路径等栈帧细节）。精选而非宽泛：文件系统路径、DSN/URL 凭据、OpenAI/httpx 异常格式、Python traceback 行。
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


OUTBOX_QUEUE_MAX = 1024


class JsonRpcDispatcher:
    def __init__(
        self,
        send_json: Callable[[dict], Awaitable[None]],
        replay_buffer: ReplayBuffer | None = None,
        send_strict: Callable[[dict], Awaitable[bool]] | None = None,
    ):
        self._send = send_json
        self._send_strict_cb = send_strict
        self._replay_buffer: ReplayBuffer | None = replay_buffer
        self._handlers: dict[str, Handler] = {}
        self._send_lock = asyncio.Lock()
        self._hold_events: bool = False
        self._hold_timeout_task: asyncio.Task | None = None
        self._outbox: asyncio.Queue[tuple[int | None, dict[str, Any], int | None]] = asyncio.Queue(
            maxsize=OUTBOX_QUEUE_MAX,
        )
        self._writer_task: asyncio.Task | None = None
        self._delivered_ids: list[int] = []
        self._pending_outbox_events: dict[int, int] = {}
        self._next_seq: int = 0

    def set_sender(
        self,
        send_json: Callable[[dict], Awaitable[None]],
        send_strict: Callable[[dict], Awaitable[bool]] | None = None,
    ) -> None:
        self._send = send_json
        if send_strict is not None:
            self._send_strict_cb = send_strict

    async def _send_strict(self, frame: dict) -> bool:
        if self._send_strict_cb is not None:
            return await self._send_strict_cb(frame)
        try:
            await self._send(frame)
            return True
        except Exception:
            return False

    def start_writer(self) -> None:
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._writer_loop())

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

    async def _writer_loop(self) -> None:
        try:
            while True:
                # 等 hold 释放再拉，避免和 flush_unsent / replay 抢同一帧的发送路径
                while self._hold_events:
                    await asyncio.sleep(0.05)
                seq, frame, event_id = await self._outbox.get()
                try:
                    async with self._send_lock:
                        if self._hold_events:
                            # 极小竞态：拉取瞬间又进 hold；该帧留给 flush_unsent 兜底，ReplayBuffer 仍为 sent=False
                            continue
                        if self._replay_buffer is not None and seq is not None:
                            buf_entry = self._replay_buffer._buffer.get(seq)
                            if buf_entry is not None and buf_entry.sent:
                                # flush_unsent / replay 已发，跳过物理发送但仍需记账
                                self._pending_outbox_events.pop(seq, None)
                                if event_id is not None:
                                    self._delivered_ids.append(event_id)
                                continue
                        success = await self._send_strict(frame)
                        if success:
                            if self._replay_buffer is not None and seq is not None:
                                self._replay_buffer.mark_sent_through(seq)
                            self._pending_outbox_events.pop(seq, None)
                            if event_id is not None:
                                self._delivered_ids.append(event_id)
                        else:
                            # 不标记 sent，留给 flush_unsent 在重连后重试；_recover_stale_locks 兜底
                            logger.warning(
                                "outbox writer send failed, deferring to stale-lock recovery",
                                extra={"seq": seq, "event_id": event_id},
                            )
                finally:
                    self._outbox.task_done()
        except asyncio.CancelledError:
            pass

    def enable_hold(self, timeout_seconds: float = 10.0) -> None:
        """激活事件 hold 模式：push_event 只追加到缓冲，等到 mount（session.resume / session.get_main / session.create）时再 flush。"""
        self._hold_events = True
        if self._hold_timeout_task and not self._hold_timeout_task.done():
            self._hold_timeout_task.cancel()

        async def _timeout_flush():
            try:
                await asyncio.sleep(timeout_seconds)
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

        handler = self._handlers.get(method)
        if handler is None:
            await self._reply_error(msg_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")
            return

        try:
            async with async_trace_span(f"rpc.{method}", attributes={"rpc.id": msg_id}):
                result = await handler(params)
        except JsonRpcError as e:
            await self._reply_error(msg_id, e.code, e.message, e.data)
            return
        except Exception as e:
            # 不外泄内部细节：完整异常记在服务端日志，向 renderer 发清洗后的标签。ARCH §11#2。
            logger.exception("jsonrpc method failed", extra={"method": method})
            label = f"{type(e).__name__}: {e}"
            await self._reply_error(msg_id, JSONRPC_INTERNAL_ERROR, redact_message(label))
            return

        if msg_id is None:
            # 通知：无 id → 调用方不期待回复。
            return
        await self._reply_result(msg_id, result)

    async def enqueue_event(
        self,
        event_type: str,
        payload: Any = None,
        session_id: str | None = None,
        event_id: int | None = None,
    ) -> bool:
        """入队事件：有 writer 时非阻塞入队；无 writer 时（测试）同步发送。返回 False 表示队列满或同步发送失败。"""
        params: dict = {"type": event_type}
        if session_id is not None:
            params["session_id"] = session_id
        if payload is not None:
            params["payload"] = payload
        frame: dict[str, Any] = {"jsonrpc": JSON_RPC_VERSION, "method": "event", "params": params}
        seq: int | None = None
        if self._replay_buffer is not None:
            seq, frame = self._replay_buffer.append(frame)
        else:
            self._next_seq += 1
            seq = self._next_seq
            if isinstance(frame.get("params"), dict):
                frame["params"]["seq"] = seq

        # 对所有入队登记 seq→event_id，让 flush_unsent 在 writer 失败时仍能回收 delivered 记账
        if event_id is not None and seq is not None:
            self._pending_outbox_events[seq] = event_id

        if self._hold_events:
            return True

        if self._writer_task is not None and not self._writer_task.done():
            try:
                self._outbox.put_nowait((seq, frame, event_id))
                return True
            except asyncio.QueueFull:
                logger.warning(
                    "outbox writer queue full, dropping event",
                    extra={"event_type": event_type, "seq": seq, "queue_size": self._outbox.qsize()},
                )
                self._pending_outbox_events.pop(seq, None)
                return False

        # 测试路径：无 writer，同步发送
        async with self._send_lock:
            success = await self._send_strict(frame)
            if success:
                if self._replay_buffer is not None and seq is not None:
                    self._replay_buffer.mark_sent_through(seq)
                self._pending_outbox_events.pop(seq, None)
                if event_id is not None:
                    self._delivered_ids.append(event_id)
            return success

    async def push_event(self, event_type: str, payload: Any = None, session_id: str | None = None) -> None:
        await self.enqueue_event(event_type, payload, session_id=session_id)

    async def push_error_event(self, message: str, session_id: str | None = None) -> None:
        # push_event 绕开 _reply_error，原始异常文本必须在此处显式 redact（ARCH §11#2）。
        await self.push_event("error", {"message": redact_message(message)}, session_id=session_id)

    async def replay(self, last_seq: int) -> list[dict[str, Any]] | None:
        """在 send lock 内执行快照+顺序 replay，然后释放 hold。"""
        if self._hold_timeout_task and not self._hold_timeout_task.done():
            self._hold_timeout_task.cancel()
            self._hold_timeout_task = None

        if self._replay_buffer is None:
            self._hold_events = False
            return None

        async with self._send_lock:
            if not self._replay_buffer.can_replay(last_seq):
                self._hold_events = False
                return None

            replayed_frames = self._replay_buffer.replay_since(last_seq) or []
            last_sent_seq: int | None = None
            for frame in replayed_frames:
                if not await self._send_strict(frame):
                    # 客户端在 replay 中途断开：剩余帧留给下次 resume，未发帧不得标记 sent——
                    # sent=True 会被 writer 的已发分支误记 delivered，令 outbox 事件失去重投。
                    break
                seq_num = (
                    frame.get("params", {}).get("seq") if isinstance(frame.get("params"), dict) else frame.get("seq")
                )
                if seq_num is not None:
                    last_sent_seq = seq_num
                    outbox_event_id = self._pending_outbox_events.pop(seq_num, None)
                    if outbox_event_id is not None:
                        self._delivered_ids.append(outbox_event_id)
            if last_sent_seq is not None:
                self._replay_buffer.mark_sent_through(last_sent_seq)

            while True:
                unsent = [f for f in self._replay_buffer.get_unsent() if f.seq > last_seq]
                if not unsent:
                    break
                failed = False
                for f in unsent:
                    if not await self._send_strict(f.frame):
                        failed = True
                        break
                    f.sent = True
                    outbox_event_id = self._pending_outbox_events.pop(f.seq, None)
                    if outbox_event_id is not None:
                        self._delivered_ids.append(outbox_event_id)
                if failed:
                    break

            self._hold_events = False
            return replayed_frames

    async def flush_unsent(self) -> None:
        """在 send lock 内按序 flush 所有未发缓冲帧，然后释放 hold。"""
        if self._hold_timeout_task and not self._hold_timeout_task.done():
            self._hold_timeout_task.cancel()
            self._hold_timeout_task = None

        if self._replay_buffer is None:
            self._hold_events = False
            return

        async with self._send_lock:
            while True:
                unsent = self._replay_buffer.get_unsent()
                if not unsent:
                    break
                failed = False
                for f in unsent:
                    if not await self._send_strict(f.frame):
                        # 发送失败不标记 sent（同 writer 失败路径的约定）：帧留给重连后的 replay/flush，
                        # DB 事件由僵尸锁恢复重投；标记 sent 会让 writer 误记 delivered 且 pending 记账永不回收。
                        failed = True
                        break
                    f.sent = True
                    outbox_event_id = self._pending_outbox_events.pop(f.seq, None)
                    if outbox_event_id is not None:
                        self._delivered_ids.append(outbox_event_id)
                if failed:
                    break
            self._hold_events = False

    async def _reply_result(self, msg_id: Any, result: Any) -> None:
        async with self._send_lock:
            await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "result": result})

    async def _reply_error(self, msg_id: Any, code: int, message: str, data: Any = None) -> None:
        # 按规范 §5.1：错误回复的 id 为请求 id 或 null（请求不可解析时）。只接受清洗后的消息——合成消息的 raise 点（handler except、session.resume "not found" 等）负责保证对用户友好；此处仍跑 redact 作为兜底。
        error = {
            "code": code,
            "message": redact_message(message),
            **({"data": _redact_data(data)} if data is not None else {}),
        }
        async with self._send_lock:
            await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "error": error})
