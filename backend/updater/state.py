"""任务状态与增量日志的持久化：专用持久卷内原子写入，保留最近 N 次。"""

import asyncio
import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from infrastructure.updater_contracts import ServiceUpdateTaskView
from pydantic import ValidationError
from updater.config import UpdaterSettings
from updater.redact import sanitize_line

STATE_FILENAME = "updater-state.json"


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaskLog:
    """单任务日志：上限截断 + 增量读取（按字节偏移）。"""

    def __init__(self, path: Path, max_bytes: int, *, truncated: bool = False) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._truncated = truncated
        self._written = self.size()
        self._lock = asyncio.Lock()

    @property
    def truncated(self) -> bool:
        return self._truncated

    def size(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    async def append(self, line: str) -> None:
        async with self._lock:
            if self._truncated:
                return
            encoded = (sanitize_line(line) + "\n").encode("utf-8", "replace")
            if self._written + len(encoded) > self._max_bytes:
                await asyncio.to_thread(self._raw_append, "…[日志达到上限，后续输出不再记录]\n".encode())
                self._truncated = True
                return
            await asyncio.to_thread(self._raw_append, encoded)
            self._written += len(encoded)

    def _raw_append(self, data: bytes) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def remove(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink()

    async def read(self, offset: int, *, max_bytes: int = 512 * 1024) -> tuple[int, int, bool, list[str]]:
        """返回 (新偏移, 总字节, 是否截断, 行列表)；offset 之后的内容。"""

        def _read() -> tuple[int, int, bool, list[str]]:
            try:
                with self._path.open("rb") as handle:
                    total = os.fstat(handle.fileno()).st_size
                    handle.seek(offset)
                    chunk = handle.read(max(0, min(max_bytes, total - offset)))
            except FileNotFoundError:
                return 0, 0, self._truncated, []
            return offset + len(chunk), total, self._truncated, chunk.decode("utf-8", "replace").splitlines()

        return await asyncio.to_thread(_read)


class TaskBusyError(RuntimeError):
    def __init__(self, active_task_id: str) -> None:
        super().__init__(f"已有任务在执行：{active_task_id}")
        self.active_task_id = active_task_id


@dataclass
class TaskRecord:
    task: ServiceUpdateTaskView
    log: TaskLog

    def view(self) -> ServiceUpdateTaskView:
        return self.task.model_copy(update={"log_offset": self.log.size(), "log_truncated": self.log.truncated})


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
        active_id = raw.get("active_task_id")
        for item in raw.get("tasks", []):
            try:
                task = ServiceUpdateTaskView.model_validate(item)
            except ValidationError:
                continue
            if task.task_id == active_id and task.state == "running":
                task.state = "interrupted"
                task.updated_at = utc_now()
            self._tasks.append(self.record(task))
        self._persist_sync(None, self._tasks)

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
                return existing, True
            if self._active is not None:
                raise TaskBusyError(self._active.task.task_id)
            tasks = [record, *self._tasks]
            kept = tasks[: self._settings.task_history_limit]
            await asyncio.to_thread(self._persist_sync, record, kept)
            self._active, self._tasks = record, kept
            for expired in tasks[len(kept) :]:
                expired.log.remove()
            return record, False

    async def finish(self) -> None:
        async with self._lock:
            self._active = None
            await asyncio.to_thread(self._persist_sync, None, self._tasks)

    def _persist_sync(self, active: TaskRecord | None, tasks: list[TaskRecord]) -> None:
        payload = {
            "active_task_id": active.task.task_id if active else None,
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
