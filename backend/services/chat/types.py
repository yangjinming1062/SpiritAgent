import asyncio
import threading
from collections.abc import Callable

TrackTask = Callable[[asyncio.Task], None]


class IterationBudget:
    """一次性计数器；``max_total`` 用尽时返回 False。"""

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)
