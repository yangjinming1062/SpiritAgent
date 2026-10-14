import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .cu_backend import ComputerUseBackend

logger = logging.getLogger(__name__)


class DesktopManager:
    def __init__(self, factory: Callable[[], ComputerUseBackend]) -> None:
        self.lock = threading.RLock()
        self._factory = factory
        self._backend: ComputerUseBackend | None = None
        self._closed = False

    def backend(self) -> ComputerUseBackend:
        with self.lock:
            if self._closed:
                raise RuntimeError("desktop automation is shutting down")
            if self._backend is None:
                backend = self._factory()
                try:
                    backend.start()
                except BaseException:
                    try:
                        backend.stop()
                    except Exception:
                        logger.warning("Desktop cleanup after startup failure failed", exc_info=True)
                    raise
                self._backend = backend
            return self._backend

    def close(self) -> None:
        with self.lock:
            self._closed = True
            backend, self._backend = self._backend, None
            if backend is not None:
                backend.stop()


_CURRENT: ContextVar[DesktopManager] = ContextVar("desktop_manager")


def current_manager() -> DesktopManager:
    return _CURRENT.get()


@contextmanager
def desktop_scope(manager: DesktopManager) -> Iterator[None]:
    token = _CURRENT.set(manager)
    try:
        yield
    finally:
        _CURRENT.reset(token)
