import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .operations import FileOperations
from .tracking import FileStateRegistry


class FileManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.operations: FileOperations | None = None
        self.state = FileStateRegistry()

    def clear(self) -> None:
        with self.lock:
            self.operations = None
            self.state.clear()

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
