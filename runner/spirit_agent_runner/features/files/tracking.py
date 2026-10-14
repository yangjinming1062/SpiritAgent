import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from spirit_agent_runner.contracts.config import cfg_get, load_config

_MAX_PATHS_PER_TASK = 4096


def _disabled() -> bool:
    return bool(cfg_get(load_config(), "file_state", "disabled", default=False))


def _safe_mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


class FileStateRegistry:
    """记录读/写版本，写入前提示外部修改；同路径读改写经 lock_path 串行。"""

    def __init__(self) -> None:
        self._reads: dict[str, dict[str, tuple[float, bool]]] = {}  # task_id → path → (mtime, 是否局部读取)
        self._path_locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def clear(self, task_id: str | None = None) -> None:
        with self._state_lock:
            if task_id is None:
                self._reads.clear()
            else:
                self._reads.pop(task_id, None)

    @contextmanager
    def lock_path(self, resolved: str) -> Iterator[None]:
        with self._meta_lock:
            lock = self._path_locks.setdefault(resolved, threading.Lock())
        with lock:
            yield

    def _record(self, task_id: str, resolved: str, *, partial: bool) -> None:
        if _disabled() or (mtime := _safe_mtime(resolved)) is None:
            return
        with self._state_lock:
            reads = self._reads.setdefault(task_id, {})
            reads.pop(resolved, None)
            reads[resolved] = (mtime, partial)
            for key in list(reads)[: max(0, len(reads) - _MAX_PATHS_PER_TASK)]:
                del reads[key]

    def record_read(self, task_id: str, resolved: str, *, partial: bool) -> None:
        self._record(task_id, resolved, partial=partial)

    def note_write(self, task_id: str, resolved: str) -> None:
        self._record(task_id, resolved, partial=False)

    def check_stale(self, task_id: str, resolved: str, *, whole_file: bool) -> str | None:
        """返回面向模型的提示；``whole_file`` 为整文件覆盖时额外提示只读过局部。"""
        if _disabled():
            return None
        with self._state_lock:
            stamp = self._reads.get(task_id, {}).get(resolved)
        if stamp is None or (current_mtime := _safe_mtime(resolved)) is None:
            return None
        if current_mtime != stamp[0]:
            return (
                f"{resolved} changed on disk after your last read of it, so your view of the file may be outdated. "
                "Re-read it to verify the result."
            )
        if whole_file and stamp[1]:
            return (
                f"{resolved} was only partially read (offset/limit) before this full overwrite. "
                "Re-read it to verify nothing was lost."
            )
        return None
