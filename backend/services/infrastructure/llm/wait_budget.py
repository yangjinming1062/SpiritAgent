"""已接单的慢速生图等待与调用方执行预算分开计时。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class _GenerationTimeout:
    timeout: asyncio.Timeout
    active: bool = True
    waiters: int = 0
    deadline: float | None = None
    started: float = 0

    def pause(self) -> bool:
        if not self.active or self.timeout.expired():
            return False
        if self.waiters == 0:
            self.deadline = self.timeout.when()
            self.started = asyncio.get_running_loop().time()
            if self.deadline is not None and self.deadline <= self.started:
                return False
            self.timeout.reschedule(None)
        self.waiters += 1
        return True

    def resume(self) -> None:
        self.waiters -= 1
        if not self.active or self.timeout.expired():
            return
        if self.waiters == 0 and self.deadline is not None:
            elapsed = asyncio.get_running_loop().time() - self.started
            self.timeout.reschedule(self.deadline + elapsed)


_GENERATION_TIMEOUTS: ContextVar[tuple[_GenerationTimeout, ...]] = ContextVar("generation_timeouts", default=())


@contextmanager
def generation_timeout_scope(timeout: asyncio.Timeout) -> Iterator[None]:
    """登记可暂停的执行预算；授权到期、用户取消与供应商等待上限由各自入口管理。"""
    budget = _GenerationTimeout(timeout)
    token = _GENERATION_TIMEOUTS.set((*_GENERATION_TIMEOUTS.get(), budget))
    try:
        yield
    finally:
        budget.active = False
        _GENERATION_TIMEOUTS.reset(token)


@contextmanager
def accepted_image_job_wait() -> Iterator[None]:
    """已确认接单的本地生图等待不消耗回合执行预算；重叠等待只计一次。"""
    budgets = tuple(budget for budget in _GENERATION_TIMEOUTS.get() if budget.pause())
    try:
        yield
    finally:
        for budget in reversed(budgets):
            budget.resume()
