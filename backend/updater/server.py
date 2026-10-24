"""更新器 HTTP 服务：仅监听容器回环与共享 Unix socket。"""

import asyncio
import contextlib
import json
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl

from pydantic import BaseModel
from updater import compose, gitops
from updater.config import LISTEN_HOST, LISTEN_PORT, UpdaterSettings
from updater.contracts import (
    ServiceRuntime,
    TaskAccepted,
    UpdateRequest,
    UpdaterLogChunk,
    UpdaterMessage,
    UpdaterStatus,
    UpdaterTaskList,
    UpdaterTaskView,
)
from updater.engine import UNAVAILABLE_CHECKS, TaskBusyError, TaskEngine, collect_preflight
from updater.state import StateStore, TaskRecord

__version__ = "1"

_MAX_BODY = 64 * 1024
_MAX_REQUEST_ID = 128
_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 409: "Conflict", 413: "Payload Too Large"}

Handler = Callable[[dict], Awaitable[object]]


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        self.body = json.dumps(data, ensure_ascii=False, default=_json_default).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"不可序列化：{type(value)!r}")


class UpdaterApp:
    def __init__(self, settings: UpdaterSettings, store: StateStore, engine: TaskEngine) -> None:
        self._settings = settings
        self._store = store
        self._engine = engine
        self._routes: dict[str, Handler] = {
            "GET /status": self._status,
            "GET /tasks": self._list_tasks,
            "POST /tasks/check": self._start_check,
            "POST /tasks/update": self._start_update,
            "GET /task": self._task_view,
            "GET /log": self._task_log,
        }

    async def dispatch(self, method: str, path: str, payload: dict) -> _Response:
        handler = self._routes.get(f"{method} {path}")
        if handler is None:
            return _Response(404, UpdaterMessage(message="未知接口。"))
        try:
            result = await handler(payload)
        except TaskBusyError as busy:
            return _Response(409, UpdaterMessage(message=str(busy)))
        except LookupError as exc:
            return _Response(404, UpdaterMessage(message=str(exc)))
        except (gitops.GitError, RuntimeError, ValueError) as exc:
            return _Response(409, UpdaterMessage(message=str(exc)[:300]))
        return _Response(200, result)

    async def _status(self, _payload: dict) -> UpdaterStatus:
        repo, issues = await collect_preflight(self._settings)
        runtime: ServiceRuntime | None = None
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            # 读不到运行状态时保留空值，不覆盖仓库与预检结论。
            runtime = await compose.read_backend(self._settings)
        tasks = self._store.tasks()
        return UpdaterStatus(
            updater_ready=not any(issue.check in UNAVAILABLE_CHECKS for issue in issues),
            version=__version__,
            repo=repo,
            runtime=runtime,
            preflight_issues=issues,
            active_task=self._store.active.view() if self._store.active else None,
            latest_task=tasks[0].view() if tasks else None,
        )

    async def _list_tasks(self, _payload: dict) -> UpdaterTaskList:
        return UpdaterTaskList(tasks=[record.view() for record in self._store.tasks()])

    async def _start_check(self, payload: dict) -> TaskAccepted:
        record, existing = await self._engine.start("check", request_id=_request_id(payload))
        return TaskAccepted(task_id=record.task_id, existing=existing)

    async def _start_update(self, payload: dict) -> TaskAccepted:
        try:
            request = UpdateRequest.model_validate(payload)
        except ValueError as exc:
            raise ValueError(f"更新请求无效：{exc}") from None
        record, existing = await self._engine.start(
            "update",
            request_id=_request_id(payload),
            target_commit=request.target_commit.lower(),
        )
        return TaskAccepted(task_id=record.task_id, existing=existing)

    async def _task_view(self, payload: dict) -> UpdaterTaskView:
        return self._require_task(payload).view()

    async def _task_log(self, payload: dict) -> UpdaterLogChunk:
        record = self._require_task(payload)
        offset = max(0, int(payload.get("offset") or 0))
        new_offset, total, truncated, lines = await record.log.read(offset)
        return UpdaterLogChunk(
            task_id=record.task_id, offset=new_offset, total_bytes=total, truncated=truncated, lines=lines
        )

    def _require_task(self, payload: dict) -> TaskRecord:
        record = self._store.find(str(payload.get("task_id") or ""))
        if record is None:
            raise LookupError("任务不存在。")
        return record


def _request_id(payload: dict) -> str:
    return str(payload.get("request_id") or "")[:_MAX_REQUEST_ID]


def _payload_from(query: str, body: bytes) -> dict:
    payload: dict = dict(parse_qsl(query))
    if body:
        decoded = json.loads(body)
        if not isinstance(decoded, dict):
            raise ValueError("请求体必须是 JSON 对象。")
        payload.update(decoded)
    return payload


async def _handle_client(app: UpdaterApp, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readline(), timeout=10.0)
        parts = head.decode("latin-1", "replace").split()
        if len(parts) < 2:
            return
        method, target = parts[0].upper(), parts[1]
        path, _, query = target.partition("?")
        content_length = 0
        while line := await asyncio.wait_for(reader.readline(), timeout=10.0):
            if line in (b"\r\n", b"\n"):
                break
            name, _, value = line.decode("latin-1", "replace").partition(":")
            if name.strip().lower() == "content-length":
                content_length = int(value.strip() or 0)
        if content_length > _MAX_BODY:
            response = _Response(413, UpdaterMessage(message="请求体过大。"))
        else:
            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=10.0) if content_length else b""
            try:
                payload = _payload_from(query, body)
            except (json.JSONDecodeError, ValueError) as exc:
                response = _Response(400, UpdaterMessage(message=f"请求体无效：{exc}"))
            else:
                response = await app.dispatch(method, path, payload)
        reason = _REASONS.get(response.status, "Error")
        writer.write(
            b"HTTP/1.1 "
            + f"{response.status} {reason}\r\n".encode()
            + b"Content-Type: application/json; charset=utf-8\r\nContent-Length: "
            + str(len(response.body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
        )
        writer.write(response.body)
        await writer.drain()
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        with contextlib.suppress(ConnectionError, OSError):
            writer.close()
            await writer.wait_closed()


def _bind_unix(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(64)
    server.set_inheritable(True)
    return server


def serve(settings: UpdaterSettings) -> None:
    """主入口：回环 TCP 与 Unix socket 两个端点共用同一处理器。"""

    store = StateStore(settings)
    store.load()
    app = UpdaterApp(settings, store, TaskEngine(settings, store))

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_client(app, reader, writer)

    async def main() -> None:
        tcp = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
        unix = await asyncio.start_server(handle, sock=_bind_unix(settings.socket_path))
        async with tcp, unix:
            await asyncio.gather(tcp.serve_forever(), unix.serve_forever())

    asyncio.run(main())
