import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .operations import FileOperations
from .tracking import FileStateRegistry


class FileManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.operations: dict[str, FileOperations] = {}
        self.state = FileStateRegistry()

    def clear(self, task_id: str | None = None) -> None:
        with self.lock:
            if task_id is None:
                self.operations.clear()
            else:
                self.operations.pop(task_id, None)
            self.state.clear(task_id)

    def close(self) -> None:
        self.clear()


_CURRENT: ContextVar[FileManager] = ContextVar("file_manager")


def current_manager() -> FileManager:
    return _CURRENT.get()


@contextmanager
def file_scope(manager: FileManager) -> Iterator[None]:
    token = _CURRENT.set(manager)
    try:
        yield
    finally:
        _CURRENT.reset(token)
