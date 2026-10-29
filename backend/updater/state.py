"""任务状态与增量日志的持久化：专用持久卷内原子写入，保留最近 N 次。"""

import asyncio
import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from updater.config import UpdaterSettings
from updater.contracts import CheckResult, TaskKind, TaskState, UpdaterError, UpdaterTaskView, UpdateStage
from updater.redact import sanitize_line

STATE_FILENAME = "updater-state.json"


def utc_now() -> datetime:
    return datetime.now(UTC)


class TaskLog:
    """单任务日志：上限截断 + 增量读取（按字节偏移）。"""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._truncated = False
        self._written = 0
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
            encoded = (sanitize_line(line) + "\n").encode("utf-8", "replace")
            if self._written + len(encoded) > self._max_bytes:
                if not self._truncated:
                    self._truncated = True
                    await asyncio.to_thread(self._raw_append, "…[日志达到上限，后续输出不再记录]\n".encode())
                return
            self._written += len(encoded)
            await asyncio.to_thread(self._raw_append, encoded)

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
                blob = self._path.read_bytes()
            except FileNotFoundError:
                return 0, 0, self._truncated, []
            chunk = blob[offset : offset + max_bytes]
            return offset + len(chunk), len(blob), self._truncated, chunk.decode("utf-8", "replace").splitlines()

        return await asyncio.to_thread(_read)


class TaskRecord:
    def __init__(self, *, task_id: str, request_id: str, kind: TaskKind, target_commit: str, log: TaskLog) -> None:
        self.task_id = task_id
        self.request_id = request_id
        self.kind = kind
        self.target_commit = target_commit
        self.state: TaskState = "running"
        self.stage: UpdateStage = "preflight"
        self.error: UpdaterError | None = None
        self.check: CheckResult | None = None
        self.created_at = utc_now()
        self.updated_at = self.created_at
        self.log = log

    def view(self) -> UpdaterTaskView:
        return UpdaterTaskView(
            task_id=self.task_id,
            request_id=self.request_id,
            kind=self.kind,
            state=self.state,
            stage=self.stage,
            target_commit=self.target_commit,
            created_at=self.created_at,
            updated_at=self.updated_at,
            error=self.error,
            log_offset=self.log.size(),
            log_truncated=self.log.truncated,
            check=self.check,
        )

    def to_json(self) -> dict:
        return json.loads(self.view().model_dump_json())


class StateStore:
    """任务列表 + 当前任务指针；写入使用临时文件 + 原子替换。"""

    def __init__(self, settings: UpdaterSettings) -> None:
        self._settings = settings
        self._root = settings.state_dir
        self._state_path = self._root / STATE_FILENAME
        self._tasks: list[TaskRecord] = []
        self._active: TaskRecord | None = None
        self._lock = asyncio.Lock()
        self._loggers: dict[str, TaskLog] = {}

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
            record = self._restore(item)
            if record is None:
                continue
            self._tasks.append(record)
            if record.task_id == active_id and record.state == "running":
                # 重启后遗留的 running 任务不可能仍执行，标记中断；运行状态由 /status 重新读取。
                record.state = "interrupted"
                record.updated_at = utc_now()
        self._persist_sync()

    def log_for(self, task_id: str) -> TaskLog:
        log = self._loggers.get(task_id)
        if log is None:
            log = TaskLog(self._root / "logs" / f"{task_id}.log", self._settings.log_max_bytes)
            self._loggers[task_id] = log
        return log

    def _restore(self, item: dict) -> TaskRecord | None:
        try:
            record = TaskRecord(
                task_id=item["task_id"],
                request_id=item.get("request_id", ""),
                kind=item["kind"],
                target_commit=item.get("target_commit", ""),
                log=self.log_for(item["task_id"]),
            )
            record.state = item["state"]
            record.stage = item["stage"]
            record.created_at = datetime.fromisoformat(item["created_at"])
            record.updated_at = datetime.fromisoformat(item["updated_at"])
            record.error = UpdaterError.model_validate(item["error"]) if item.get("error") else None
            record.check = CheckResult.model_validate(item["check"]) if item.get("check") else None
            return record
        except (KeyError, ValueError):
            return None

    @property
    def active(self) -> TaskRecord | None:
        return self._active

    async def begin(self, record: TaskRecord) -> None:
        async with self._lock:
            self._active = record
            self._tasks.insert(0, record)
            self._trim()
            await self._persist()

    async def finish(self) -> None:
        async with self._lock:
            self._active = None
            await self._persist()

    def _trim(self) -> None:
        limit = self._settings.task_history_limit
        if len(self._tasks) <= limit:
            return
        for record in self._tasks[limit:]:
            self.log_for(record.task_id).remove()
            self._loggers.pop(record.task_id, None)
        self._tasks = self._tasks[:limit]

    async def _persist(self) -> None:
        await asyncio.to_thread(self._persist_sync)

    def _persist_sync(self) -> None:
        payload = {
            "active_task_id": self._active.task_id if self._active else None,
            "tasks": [record.to_json() for record in self._tasks],
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
        return next((record for record in self._tasks if record.task_id == task_id), None)

    def find_by_request(self, request_id: str) -> TaskRecord | None:
        return next((record for record in self._tasks if record.request_id and record.request_id == request_id), None)
