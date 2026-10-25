import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from infrastructure.api_errors import api_error
from infrastructure.async_io import cancel_and_wait, cancel_tasks
from infrastructure.constants import (
    JSON_RPC_VERSION,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
)
from infrastructure.logger import get_logger
from pydantic import JsonValue

from runtime.desktop.buffer import BufferedFrame, ReplayBuffer

logger = get_logger(__name__)

OUTBOX_QUEUE_MAX = 1024


_EVENT_SERIALIZER: Callable[[str, Any], dict[str, JsonValue]] | None = None


def configure_event_serializer(serializer: Callable[[str, Any], dict[str, JsonValue]]) -> None:
    global _EVENT_SERIALIZER
    _EVENT_SERIALIZER = serializer


class JsonRpcError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        details: JsonValue = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


# Handler 返回 JSON-RPC result。raise JsonRpcError 会以结构化错误回复；其他异常统一变 -32603。
Handler = Callable[[dict[str, Any]], Awaitable[Any]]

# 发送单个 JSON 帧，返回是否确认写出；连接已断开时返回 False 而不抛出。
Sender = Callable[[dict[str, Any]], Awaitable[bool]]

_HOLD_TIMEOUT_SECONDS = 10.0


class JsonRpcDispatcher:
    def __init__(self, send: Sender) -> None:
        self.stream_id = uuid4().hex
        self._sender: Sender | None = send
        self.replay_buffer = ReplayBuffer()
        self._handlers: dict[str, Handler] = {}
        self._concurrent_methods: set[str] = set()
        self._request_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._hold_events: bool = False
        self._hold_timeout_task: asyncio.Task | None = None
        self._outbox: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue(maxsize=OUTBOX_QUEUE_MAX)
        self._writer_task: asyncio.Task | None = None
        self._delivered_ids: list[int] = []
        self._outbox_event_ids: dict[int, int] = {}
        self._queued_sequences: set[int] = set()
        self._pending_outbox_events: dict[
            int,
            int,
        ] = {}  # seq → outbox 事件 ID：任一发送路径送达时回收为 delivered 记账

    def set_sender(self, send: Sender | None) -> None:
        self._sender = send

    async def _send(self, frame: dict[str, Any]) -> bool:
        sender = self._sender
        return await sender(frame) if sender is not None else False

    def start_writer(self) -> None:
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._writer_loop())

    async def stop_writer(self) -> None:
        tasks = [task for task in (self._writer_task, self._hold_timeout_task) if task is not None]
        self._writer_task = self._hold_timeout_task = None
        await cancel_and_wait(tasks)

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
                            self.replay_buffer.mark_sent(seq)
                            self._record_delivered(seq)
                        else:
                            # 不标记 sent，留给重连后的 flush_unsent；outbox 事件由僵尸锁恢复重投
                            logger.warning(
                                "outbox writer send failed, deferring to stale-lock recovery",
                                extra={"seq": seq, "event_id": self._pending_outbox_events.get(seq)},
                            )
                finally:
                    self._queued_sequences.discard(seq)
                    self._outbox.task_done()
        except asyncio.CancelledError:
            pass

    def _cancel_hold_timeout(self) -> None:
        task = self._hold_timeout_task
        self._hold_timeout_task = None
        # 超时任务自身触发 flush 时不能自我取消，否则 flush 在首个挂起点中断、hold 永不释放
        cancel_tasks((task,))

    def enable_hold(self) -> None:
        """连接恢复前暂存事件，gateway.resume 重放或冲刷后恢复交付。"""
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

    def register(self, method: str, handler: Handler, *, concurrent: bool = False) -> None:
        """仅不依赖普通请求执行顺序的处理器可并发运行。"""
        self._handlers[method] = handler
        if concurrent:
            self._concurrent_methods.add(method)
        else:
            self._concurrent_methods.discard(method)

    async def handle_raw(self, data: str) -> None:
        """解析原始 WS 帧并派发：JSON 非法返回 -32700，非对象返回 -32600；错误回复 id 按 JSON-RPC 2.0 §5.1 为 null。"""
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
        method = msg.get("method")
        if isinstance(method, str) and method in self._concurrent_methods:
            await self._handle(msg)
        else:
            async with self._request_lock:
                await self._handle(msg)

    async def _handle(self, msg: dict) -> None:
        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {})
        if msg.get("jsonrpc") != JSON_RPC_VERSION:
            await self._reply_error(None, JSONRPC_INVALID_REQUEST, "jsonrpc must be 2.0")
            return
        if "id" in msg and msg_id is not None and (type(msg_id) not in (str, int)):
            await self._reply_error(None, JSONRPC_INVALID_REQUEST, "id must be a string, integer or null")
            return
        if not isinstance(method, str) or not method:
            await self._reply_error(msg_id, JSONRPC_INVALID_REQUEST, "method must be a non-empty string")
            return
        is_notification = "id" not in msg
        if not isinstance(params, dict):
            if is_notification:
                logger.warning("jsonrpc notification params must be an object", extra={"method": method})
            else:
                await self._reply_error(msg_id, JSONRPC_INVALID_PARAMS, "params must be an object")
            return

        handler = self._handlers.get(method)
        if handler is None:
            if is_notification:
                logger.warning("jsonrpc notification for unknown method", extra={"method": method})
                return
            await self._reply_error(msg_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}")
            return

        try:
            result = await handler(params)
        except JsonRpcError as e:
            if is_notification:
                logger.warning("jsonrpc notification rejected", extra={"method": method, "code": e.code})
                return
            await self._reply_error(msg_id, e.code, e.message, details=e.details)
            return
        except Exception as e:
            # 不外泄内部细节：完整异常记在服务端日志，向 renderer 发清洗后的标签（PROTOCOL「错误信封」）
            logger.exception("jsonrpc method failed", extra={"method": method})
            if is_notification:
                return
            label = f"{type(e).__name__}: {e}"
            await self._reply_error(msg_id, JSONRPC_INTERNAL_ERROR, label)
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
        """新事件入缓冲后交给 writer；重投复用原帧，排队失败返回 False。"""
        buffered = None
        previous_seq = self._outbox_event_ids.get(event_id) if event_id is not None else None
        if event_id is not None and previous_seq is not None:
            if previous_seq not in self._pending_outbox_events:
                # 物理送达之后写库可能失败；重投只补送达标记，不重复发送客户端事件。
                if event_id not in self._delivered_ids:
                    self._delivered_ids.append(event_id)
                return True
            if previous_seq in self._queued_sequences:
                return True
            buffered = self.replay_buffer.get_frame(previous_seq)
        if buffered is None and _EVENT_SERIALIZER is not None:
            payload = _EVENT_SERIALIZER(event_type, payload)
        if session_id is None and isinstance(payload, dict) and isinstance(payload.get("session_id"), str):
            session_id = payload["session_id"]
        writer_running = self._writer_task is not None and not self._writer_task.done()
        # 判定到入队之间没有 await，拒绝的 tool.call 不分配序号、不进入重放缓冲。
        if not self._hold_events and writer_running and self._outbox.full():
            logger.warning(
                "outbox writer queue full, rejecting event",
                extra={"event_type": event_type, "queue_size": self._outbox.qsize()},
            )
            return False
        if buffered is None:
            params: dict[str, Any] = {"type": event_type, "stream_id": self.stream_id}
            if session_id is not None:
                params["session_id"] = session_id
            if payload is not None:
                params["payload"] = payload
            seq, frame = self.replay_buffer.append({"jsonrpc": JSON_RPC_VERSION, "method": "event", "params": params})
            if event_id is not None:
                if previous_seq is not None:
                    self._pending_outbox_events.pop(previous_seq, None)
                self._pending_outbox_events[seq] = event_id
                self._outbox_event_ids[event_id] = seq
                if len(self._outbox_event_ids) > OUTBOX_QUEUE_MAX * 2:
                    self._outbox_event_ids.pop(next(iter(self._outbox_event_ids)))
        else:
            # writer 发送失败后复用原序号重排，避免制造缺口触发完整状态恢复。
            seq, frame = buffered.seq, buffered.frame

        if self._hold_events:
            return True

        if writer_running:
            self._queued_sequences.add(seq)
            self._outbox.put_nowait((seq, frame))
            return True

        # writer 未运行（注销后仍被在途任务引用）：同步发送
        self._queued_sequences.add(seq)
        try:
            async with self._send_lock:
                if not await self._send(frame):
                    self.replay_buffer.discard(seq)
                    self._pending_outbox_events.pop(seq, None)
                    if event_id is not None:
                        self._outbox_event_ids.pop(event_id, None)
                    return False
                self.replay_buffer.mark_sent(seq)
                self._record_delivered(seq)
                return True
        finally:
            self._queued_sequences.discard(seq)

    async def push_event(self, event_type: str, payload: Any = None, session_id: str | None = None) -> None:
        await self.enqueue_event(event_type, payload, session_id=session_id)

    async def _send_frames(self, frames: list[BufferedFrame]) -> bool:
        """按序发送缓冲帧并记账；发送失败即停，未发帧不得标记 sent，否则 writer 会误记 delivered、outbox 事件失去重投。"""
        for f in frames:
            if not await self._send(f.frame):
                return False
            self.replay_buffer.mark_sent(f.seq)
            self._record_delivered(f.seq)
        return True

    async def _flush_locked(self, after_seq: int) -> bool:
        # 外层循环覆盖发送挂起期间新入缓冲的帧。
        while unsent := self.replay_buffer.get_unsent(after_seq):
            if not await self._send_frames(unsent):
                return False
        return True

    async def replay(self, last_seq: int) -> int | None:
        """在 send lock 内重放 last_seq 之后的帧、补发新帧并释放 hold，返回重放帧数；缓冲已不覆盖 last_seq 时返回 None 且保持 hold。"""
        async with self._send_lock:
            frames = self.replay_buffer.replay_since(last_seq)
            if frames is None:
                return None
            self._cancel_hold_timeout()
            if not await self._send_frames(frames) or not await self._flush_locked(last_seq):
                return None
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

    async def _reply_error(
        self,
        msg_id: Any,
        code: int,
        message: str,
        *,
        details: JsonValue = None,
    ) -> None:
        public = api_error(
            400 if code != JSONRPC_INTERNAL_ERROR else 500,
            message,
            code=f"rpc_{abs(code)}",
            details=details,
        )
        error = {"code": code, "message": public.message, "data": public.model_dump(mode="json")}
        async with self._send_lock:
            await self._send({"jsonrpc": JSON_RPC_VERSION, "id": msg_id, "error": error})
