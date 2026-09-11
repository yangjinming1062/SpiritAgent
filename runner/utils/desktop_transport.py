"""Runner 与 Desktop 之间的 OS 原生 IPC 传输层（命名管道 / Unix 域套接字）。

底层走字节流，上层复用 sans-I/O 的 ``websockets.client.ClientProtocol``：库负责握手、掩码、控制帧、关闭语义，本模块只负责字节流与协议对象之间的搬运。
"""

import asyncio
import contextlib
import ctypes
import json
import logging
import threading
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol

from websockets.client import ClientProtocol
from websockets.frames import OP_BINARY, OP_CONT, OP_TEXT, Frame
from websockets.http11 import Response
from websockets.protocol import SEND_EOF, State
from websockets.uri import parse_uri

from .constants import IS_WINDOWS, get_spiritagent_home
from .pid import PidState, pid_state

logger = logging.getLogger("spiritagent_runner.transport")

PIPE_TRANSPORT = "pipe"
UNIX_TRANSPORT = "unix"

HANDSHAKE_AUTH_HEADER = "X-SpiritAgent-Auth"

# 反向 RPC 的视觉负载上限 10 MiB（Desktop 端 reverse-rpc.cjs 的限制）；sans-I/O 默认 1 MiB 会截断，所以放宽到 16 MiB。
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

# Host 头不会出本机，用固定虚拟 URI 给协议构造器提供握手所需的元信息即可。
_DUMMY_URI = "ws://spiritagent/rpc"

_READ_CHUNK = 65536

_WIN_ERROR_FILE_NOT_FOUND = 2
_WIN_ERROR_PIPE_BUSY = 231
_WIN_ERROR_IO_PENDING = 997
_PIPE_CONNECT_TIMEOUT_S = 2.0

if IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _kernel32.CreateFileW.restype = ctypes.c_void_p
    _kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, wintypes.DWORD]
    _kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.WriteFile.restype = wintypes.BOOL
    _kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        ctypes.c_wchar_p,
    ]
    _kernel32.CreateEventW.restype = ctypes.c_void_p
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _kernel32.CancelIoEx.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _GENERIC_READ_WRITE = 0xC0000000
    _OPEN_EXISTING = 3
    _FILE_FLAG_OVERLAPPED = 0x40000000


@dataclass(frozen=True)
class DesktopEndpoint:
    """已解析的 Desktop 连接端点（来自命令行参数或端点文件）。"""

    transport: str
    path: str
    token: str
    pid: int | None = None


def read_endpoint() -> DesktopEndpoint | None:
    """读取 ``$SPIRITAGENT_HOME/desktop-endpoint.json`` 为 DesktopEndpoint；缺失/格式错误/进程已退出时返回 ``None``。"""
    try:
        endpoint_path = get_spiritagent_home() / "desktop-endpoint.json"
        if not endpoint_path.exists():
            return None
        raw = endpoint_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        expected = PIPE_TRANSPORT if IS_WINDOWS else UNIX_TRANSPORT
        transport = data.get("transport")
        path = data.get("path")
        token = data.get("token")
        pid = data.get("pid")
        if transport != expected:
            return None
        if not isinstance(path, str) or not path:
            return None
        if not isinstance(token, str) or not token:
            return None
        if isinstance(pid, int) and pid > 0 and pid_state(pid) is PidState.NOT_FOUND:
            return None
        return DesktopEndpoint(
            transport=transport,
            path=path,
            token=token,
            pid=pid if isinstance(pid, int) else None,
        )
    except (ValueError, OSError) as exc:
        logger.warning("read_endpoint encountered %s: %s", type(exc).__name__, exc)
        return None
    except Exception as exc:
        logger.warning("read_endpoint unexpected failure: %s", exc)
        return None


class _Stream(Protocol):
    """两种平台传输共用的双工字节流接口。"""

    async def read(self, n: int = _READ_CHUNK) -> bytes: ...

    async def write(self, data: bytes) -> None: ...

    async def close(self) -> None: ...


if IS_WINDOWS:

    def _connect_pipe_handle(
        path: str,
        *,
        timeout_s: float = _PIPE_CONNECT_TIMEOUT_S,
    ) -> int:
        """打开带重叠 IO 的管道客户端句柄，对连接竞争做有限重试。"""
        deadline = time.monotonic() + timeout_s
        last_error: int | None = None
        while time.monotonic() < deadline:
            handle = _kernel32.CreateFileW(
                path,
                _GENERIC_READ_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_OVERLAPPED,
                None,
            )
            if handle and handle != _INVALID_HANDLE:
                return handle
            last_error = ctypes.get_last_error()
            if last_error == _WIN_ERROR_PIPE_BUSY:
                _kernel32.WaitNamedPipeW(path, 500)
            elif last_error == _WIN_ERROR_FILE_NOT_FOUND:
                time.sleep(0.05)
            else:
                raise OSError(last_error, f"CreateFileW({path!r}) failed")
        last_error = last_error if last_error is not None else _WIN_ERROR_FILE_NOT_FOUND
        raise TimeoutError(
            f"named pipe {path!r} not connectable within {timeout_s}s (winerror {last_error})",
        )

    class _PipeStream:
        """基于重叠 IO 命名管道句柄的双工流。"""

        def __init__(self, handle: int) -> None:
            self._handle = handle
            self._loop = asyncio.get_running_loop()
            self._reader = asyncio.StreamReader(limit=MAX_MESSAGE_BYTES)
            self._write_lock = asyncio.Lock()
            self._closed = False
            self._thread: threading.Thread | None = None

        async def start(self) -> None:
            self._thread = threading.Thread(
                target=self._read_pump,
                name="desktop-pipe-reader",
                daemon=True,
            )
            self._thread.start()

        def _read_once(self) -> bytes | None:
            """执行一次重叠读；EOF、取消或管道断开时返回 ``None``。"""
            buf = ctypes.create_string_buffer(_READ_CHUNK)
            count = wintypes.DWORD(0)
            overlapped = _OVERLAPPED()
            overlapped.hEvent = _kernel32.CreateEventW(None, True, False, None)
            if not overlapped.hEvent:
                return None
            try:
                ok = _kernel32.ReadFile(
                    self._handle,
                    buf,
                    _READ_CHUNK,
                    ctypes.byref(count),
                    ctypes.byref(overlapped),
                )
                if not ok:
                    if ctypes.get_last_error() != _WIN_ERROR_IO_PENDING:
                        return None
                    ok = _kernel32.GetOverlappedResult(
                        self._handle,
                        ctypes.byref(overlapped),
                        ctypes.byref(count),
                        True,
                    )
                    if not ok:
                        return None
                return buf.raw[: count.value]
            finally:
                _kernel32.CloseHandle(overlapped.hEvent)

        def _read_pump(self) -> None:
            loop = self._loop
            reader = self._reader
            try:
                while True:
                    chunk = self._read_once()
                    if not chunk:
                        break
                    loop.call_soon_threadsafe(reader.feed_data, chunk)
            finally:
                # feed_eof 唤醒 read() 中阻塞的 pump，避免半关闭管道上永久挂起。
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(reader.feed_eof)

        async def read(self, n: int = _READ_CHUNK) -> bytes:
            return await self._reader.read(n)

        async def write(self, data: bytes) -> None:
            async with self._write_lock:
                # 句柄可能在本协程取锁过程中被关闭——若不显式检查，executor 线程里写入失败时报错会很迷惑。
                if self._closed:
                    raise ConnectionError("desktop pipe is closed")
                await self._loop.run_in_executor(None, self._write_all, data)

        def _write_all(self, data: bytes) -> None:
            buf = ctypes.create_string_buffer(data, len(data))
            count = wintypes.DWORD(0)
            overlapped = _OVERLAPPED()
            overlapped.hEvent = _kernel32.CreateEventW(None, True, False, None)
            if not overlapped.hEvent:
                raise OSError("CreateEventW failed for pipe write")
            try:
                ok = _kernel32.WriteFile(
                    self._handle,
                    buf,
                    len(data),
                    ctypes.byref(count),
                    ctypes.byref(overlapped),
                )
                if not ok:
                    error = ctypes.get_last_error()
                    if error != _WIN_ERROR_IO_PENDING:
                        raise OSError(error, "WriteFile to desktop pipe failed")
                    if not _kernel32.GetOverlappedResult(
                        self._handle,
                        ctypes.byref(overlapped),
                        ctypes.byref(count),
                        True,
                    ):
                        raise OSError(
                            ctypes.get_last_error(),
                            "GetOverlappedResult for pipe write failed",
                        )
            finally:
                _kernel32.CloseHandle(overlapped.hEvent)

        async def close(self) -> None:
            if self._closed:
                return
            self._closed = True  # 先置位：让排队等锁的写协程快速失败
            # 取消所有进行中的重叠操作（阻塞中的读 + in-flight 写），对应 GetOverlappedResult 返回 ERROR_OPERATION_ABORTED 而退出。
            _kernel32.CancelIoEx(self._handle, None)
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                # 把 join 放到 executor 中避免卡死事件循环；daemon 标志是 join 超时后的兜底。
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._loop.run_in_executor(None, thread.join, 2.0),
                        2.5,
                    )
            async with self._write_lock:  # 等待 in-flight executor 写入完成
                _kernel32.CloseHandle(self._handle)

else:

    class _UnixStream:
        """把 macOS UDS 流包装为与 ``_PipeStream`` 同形接口的适配器。"""

        def __init__(
            self,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            self._reader = reader
            self._writer = writer
            self._closed = False

        async def read(self, n: int = _READ_CHUNK) -> bytes:
            return await self._reader.read(n)

        async def write(self, data: bytes) -> None:
            if self._closed:
                raise ConnectionError("desktop socket is closed")
            self._writer.write(data)
            await self._writer.drain()

        async def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            writer = self._writer
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def _open_stream(path: str) -> _Stream:
    if IS_WINDOWS:
        # 连接循环最长会睡到 deadline（BUSY / 重启窗口），所以放到线程里执行，避免阻塞事件循环。
        handle = await asyncio.to_thread(_connect_pipe_handle, path)
        stream = _PipeStream(handle)
        await stream.start()
        return stream
    reader, writer = await asyncio.open_unix_connection(path)
    return _UnixStream(reader, writer)


class DesktopConnection:
    """基于 OS 原生 IPC 字节流的 WebSocket 客户端。

    两条必须遵守的驱动规则（违反任一条都会让协议僵死）：

    - 每次 ``receive_data()`` 及发送操作后必须 drain 一次 ``data_to_send()`` —— Pong 回复与 Close ACK 都从这里排队发出。
    - 流到达 EOF 时必须调用 ``receive_eof()``。
    """

    def __init__(self, stream: _Stream, token: str) -> None:
        self._stream = stream
        self._protocol = ClientProtocol(
            parse_uri(_DUMMY_URI),
            max_size=MAX_MESSAGE_BYTES,
        )
        self._token = token
        self._messages: asyncio.Queue[str | None] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._fragments: list[bytes] = []
        self._message_opcode: int | None = None

    async def _drain(self) -> None:
        """把协议排队中需要发出的数据（帧、Pong、Close ACK）全部写出。"""
        for data in self._protocol.data_to_send():
            if data == SEND_EOF:
                # 写入端 EOF 哨兵；管道/UDS 不能半关闭，统一由外层 stream.close() 兜底。
                continue
            await self._stream.write(data)

    async def _handshake(self) -> None:
        request = self._protocol.connect()
        request.headers[HANDSHAKE_AUTH_HEADER] = self._token
        self._protocol.send_request(request)
        await self._drain()
        while True:
            chunk = await self._stream.read()
            if not chunk:
                # 升级被拒（如 HTTP 401 + 连接关闭且无 Content-Length）只有在流 EOF 时才能终止无 body 响应并解析完成；把 EOF 喂给协议，再尝试从事件里找 Response。
                self._protocol.receive_eof()
                completed = self._consume_handshake_events(
                    self._protocol.events_received(),
                )
                await self._drain()
                if not completed:
                    raise ConnectionError(
                        "Desktop closed the IPC stream during handshake",
                    )
                self._recv_task = asyncio.create_task(
                    self._pump(),
                    name="desktop-ipc-recv",
                )
                return
            self._protocol.receive_data(chunk)
            await self._drain()
            completed = self._consume_handshake_events(self._protocol.events_received())
            await self._drain()
            if completed:
                self._recv_task = asyncio.create_task(
                    self._pump(),
                    name="desktop-ipc-recv",
                )
                return

    def _consume_handshake_events(self, events: list[Any] | tuple[Any, ...]) -> bool:
        """处理一批事件：先取 Response，再处理对端紧随其后管线化的帧。sans-I/O 解析器在 101 之后会继续解析同一批数据，而 ``events_received()`` 一次性排空队列——停在 Response 会静默丢弃后续帧。帧按协议不能在握手响应之前出现，先出现的视为协议违规。"""
        response_seen = False
        for event in events:
            if not response_seen:
                if not isinstance(event, Response):
                    raise ConnectionError(
                        f"unexpected event during handshake: {event!r}",
                    )
                self._process_handshake_response(event)
                response_seen = True
                continue
            if isinstance(event, Frame):
                self._handle_frame(event)
        return response_seen

    def _process_handshake_response(self, response: Response) -> None:
        # 非 101 时抛 InvalidStatus——例如 Desktop 返回的 HTTP 401 鉴权拒绝。调用方此时应丢弃缓存端点、重读端点文件，而不是带着过期 token 重试。
        self._protocol.process_response(response)

    async def _pump(self) -> None:
        """把字节流喂给协议，再把组装完成的消息推到队列。"""
        try:
            while True:
                chunk = await self._stream.read()
                if not chunk:
                    self._protocol.receive_eof()
                    await self._drain()
                    break
                self._protocol.receive_data(chunk)
                await self._drain()
                for event in self._protocol.events_received():
                    if isinstance(event, Frame):
                        self._handle_frame(event)
                await self._drain()
                if self._protocol.state is State.CLOSED:
                    break
        except (ConnectionError, OSError):
            pass
        except Exception as exc:
            # 协议级错误（坏 UTF-8、帧错误）按传输失败处理；finally 中的哨兵负责唤醒所有等待者。
            logger.warning("desktop IPC pump terminated: %s", exc)
        finally:
            self._messages.put_nowait(None)

    def _handle_frame(self, event: Frame) -> None:
        opcode = event.opcode
        if opcode is OP_TEXT or opcode is OP_BINARY:
            if self._message_opcode is not None:
                if self._protocol.state is State.OPEN:
                    self._protocol.send_close(
                        code=1002,
                        reason="protocol error: interleaved message during fragmentation",
                    )
                self._fragments = []
                self._message_opcode = None
                return
            if event.fin:
                self._emit(opcode, event.data)
            else:
                self._fragments = [event.data]
                self._message_opcode = opcode
        elif opcode is OP_CONT:
            if self._message_opcode is None:
                if self._protocol.state is State.OPEN:
                    self._protocol.send_close(code=1002, reason="protocol error: unexpected continuation frame")
                return
            self._fragments.append(event.data)
            if event.fin:
                self._emit(self._message_opcode, b"".join(self._fragments))
                self._fragments = []
                self._message_opcode = None
        # 控制帧永远不会以消息形式出现：Pong/Close 已经在 _drain() 里入队。

    def _emit(self, opcode: int, data: bytes) -> None:
        if opcode is OP_TEXT:
            self._messages.put_nowait(data.decode("utf-8"))
        else:
            # JSON-RPC 走文本帧；但二进制也尝试解码，避免对端 bug 被静默丢弃而表现成 JSON 解析错误。
            self._messages.put_nowait(data.decode("utf-8", errors="replace"))

    async def send(self, message: str) -> None:
        state = self._protocol.state
        if state is State.OPEN:
            self._protocol.send_text(message.encode("utf-8"))
            await self._drain()
        elif state is State.CLOSED:
            raise self._protocol.close_exc
        else:
            raise ConnectionError("Desktop connection is closing")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self._protocol.state is State.OPEN:
            self._protocol.send_close(code, reason)
            with contextlib.suppress(Exception):
                await self._drain()
        task = self._recv_task
        if task is not None and not task.done():
            # 给对端一个回送 Close 帧的窗口期——对端在同机，2 秒已偏宽松；下方 stream.close() 兜底强制解套。
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 2.0)
        await self._stream.close()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(task, 1.0)
        elif task is None:
            # 握手未完成，仍要放一个哨兵让任何等待者退出。
            self._messages.put_nowait(None)

    @property
    def close_code(self) -> int | None:
        if self._protocol.close_rcvd is not None:
            return self._protocol.close_rcvd.code
        if self._protocol.close_sent is not None:
            return self._protocol.close_sent.code
        return self._protocol.close_code

    @property
    def close_reason(self) -> str | None:
        if self._protocol.close_rcvd is not None:
            return self._protocol.close_rcvd.reason
        if self._protocol.close_sent is not None:
            return self._protocol.close_sent.reason
        return self._protocol.close_reason

    def __aiter__(self) -> "DesktopConnection":
        return self

    async def __anext__(self) -> str:
        # 连接关闭后仍要排空队列，避免 in-flight JSON-RPC 帧因拆解竞争丢失。
        if self._protocol.state is State.CLOSED and self._messages.empty():
            raise StopAsyncIteration
        item = await self._messages.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def __aenter__(self) -> "DesktopConnection":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()


async def connect_desktop(
    endpoint: DesktopEndpoint,
    *,
    open_timeout_s: float = 8.0,
) -> DesktopConnection:
    """连接 Desktop 端 IPC 端点并完成握手鉴权。"""
    stream = await _open_stream(endpoint.path)
    try:
        connection = DesktopConnection(stream, token=endpoint.token)
        await asyncio.wait_for(connection._handshake(), open_timeout_s)
    except BaseException:
        with contextlib.suppress(Exception):
            await stream.close()
        raise
    return connection
