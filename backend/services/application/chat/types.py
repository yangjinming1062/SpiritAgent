import asyncio
from collections.abc import Callable

TrackTask = Callable[[asyncio.Task], None]


class IterationBudget:
    """一次性计数器；``max_total`` 用尽时返回 False。每回合新建，单事件循环内使用，无需加锁。"""

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0

    def consume(self) -> bool:
        if self._used >= self.max_total:
            return False
        self._used += 1
        return True
