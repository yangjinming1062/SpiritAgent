"""任务状态与增量日志的持久化：专用持久卷内原子写入，保留最近 N 次。"""

import asyncio
import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from infrastructure.async_io import run_blocking
from infrastructure.updater_contracts import ServiceUpdateTaskView
from pydantic import ValidationError
from updater.config import UpdaterSettings
from updater.redact import sanitize_line

STATE_FILENAME = "updater-state.json"
_LIMIT_NOTE = "…[日志达到上限，后续输出不再记录]\n".encode()


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaskLog:
    """单任务日志：上限截断 + 增量读取（按字节偏移）。"""

    def __init__(self, path: Path, max_bytes: int, *, truncated: bool = False) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._truncated = truncated
        try:
            with path.open("rb") as handle:
                self._written = handle.seek(0, os.SEEK_END)
                if self._written >= len(_LIMIT_NOTE):
                    handle.seek(-len(_LIMIT_NOTE), os.SEEK_END)
                    self._truncated |= handle.read() == _LIMIT_NOTE
        except FileNotFoundError:
            self._written = 0
        self._lock = asyncio.Lock()

    @property
    def truncated(self) -> bool:
        return self._truncated

    @property
    def size(self) -> int:
        return self._written

    async def append(self, line: str) -> None:
        async with self._lock:
            if self._truncated:
                return
            encoded = (sanitize_line(line) + "\n").encode("utf-8", "replace")
            if self._written + len(encoded) > self._max_bytes - len(_LIMIT_NOTE):
                self._truncated = True
                encoded = _LIMIT_NOTE if self._written + len(_LIMIT_NOTE) <= self._max_bytes else b""
            if encoded:
                await run_blocking(self._raw_append, encoded)

    def _raw_append(self, data: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self._written += len(data)

    def remove(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink()

    async def read(self, offset: int, *, max_bytes: int = 512 * 1024) -> tuple[int, int, bool, list[str]]:
        """返回 (新偏移, 总字节, 是否截断, 行列表)；offset 之后的内容。"""

        def _read() -> tuple[int, int, bool, list[str]]:
            try:
                with self._path.open("rb") as handle:
                    total = os.fstat(handle.fileno()).st_size
                    start = max(0, min(offset, total))
                    if start:
                        handle.seek(start - 1)
                        if handle.read(1) != b"\n":
                            handle.readline()
                    start = handle.tell()
                    chunk = handle.read(max_bytes)
                    if chunk and not chunk.endswith(b"\n") and handle.tell() < total:
                        boundary = chunk.rfind(b"\n") + 1
                        chunk = chunk[:boundary] if boundary else chunk + handle.readline()
                    return start + len(chunk), total, self._truncated, chunk.decode("utf-8", "replace").splitlines()
            except FileNotFoundError:
                return 0, 0, self._truncated, []

        async with self._lock:
            return await run_blocking(_read)


class TaskBusyError(RuntimeError):
    def __init__(self, active_task_id: str) -> None:
        super().__init__(f"已有任务在执行：{active_task_id}")
        self.active_task_id = active_task_id


@dataclass
class TaskRecord:
    task: ServiceUpdateTaskView
    log: TaskLog

    def view(self) -> ServiceUpdateTaskView:
        return self.task.model_copy(update={"log_offset": self.log.size, "log_truncated": self.log.truncated})


class StateStore:
    """任务列表 + 当前任务指针；写入使用临时文件 + 原子替换。"""

    def __init__(self, settings: UpdaterSettings) -> None:
        self._settings = settings
        self._root = settings.state_dir
        self._state_path = self._root / STATE_FILENAME
        self._tasks: list[TaskRecord] = []
        self._active: TaskRecord | None = None
        self._lock = asyncio.Lock()

    def load(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        self._tasks.clear()
        for item in raw.get("tasks", []):
            try:
                task = ServiceUpdateTaskView.model_validate(item)
            except ValidationError:
                continue
            if task.state == "running":
                task.state = "interrupted"
                task.updated_at = utc_now()
            self._tasks.append(self.record(task))
        self._persist_sync(self._tasks)

    def record(self, task: ServiceUpdateTaskView) -> TaskRecord:
        log = TaskLog(
            self._root / "logs" / f"{task.task_id}.log", self._settings.log_max_bytes, truncated=task.log_truncated
        )
        return TaskRecord(task, log)

    @property
    def active(self) -> TaskRecord | None:
        return self._active

    async def begin(self, record: TaskRecord) -> tuple[TaskRecord, bool]:
        async with self._lock:
            if record.task.request_id and (existing := self.find_by_request(record.task.request_id)):
                if (existing.task.kind, existing.task.target_commit) != (record.task.kind, record.task.target_commit):
                    raise ValueError("同一 request_id 已用于不同的更新请求。")
                return existing, True
            if self._active is not None:
                raise TaskBusyError(self._active.task.task_id)
            tasks = [record, *self._tasks]
            kept = tasks[: self._settings.task_history_limit]
            await run_blocking(self._persist_sync, kept)
            self._active, self._tasks = record, kept
            for expired in tasks[len(kept) :]:
                expired.log.remove()
            return record, False

    async def checkpoint(self) -> None:
        async with self._lock:
            await run_blocking(self._persist_sync, self._tasks)

    async def finish(self) -> None:
        async with self._lock:
            self._active = None
            await run_blocking(self._persist_sync, self._tasks)

    def _persist_sync(self, tasks: list[TaskRecord]) -> None:
        payload = {
            "tasks": [record.view().model_dump(mode="json") for record in tasks],
        }
        self._root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._root, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._state_path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def tasks(self) -> list[TaskRecord]:
        return list(self._tasks)

    def find(self, task_id: str) -> TaskRecord | None:
        return next((record for record in self._tasks if record.task.task_id == task_id), None)

    def find_by_request(self, request_id: str) -> TaskRecord | None:
        return next(
            (record for record in self._tasks if record.task.request_id and record.task.request_id == request_id), None
        )
