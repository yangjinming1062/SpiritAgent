import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any


class BackgroundTask:
    """封装单个长生命周期 asyncio.Task 与 done 回调；统一各后台循环（scheduler、ws_event_loop）的 start/stop 形态。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self._task: asyncio.Task | None = None
        self._logger = logging.getLogger(name)

    def start(self, coro: Coroutine[Any, Any, Any]) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(coro, name=self._name)
        self._task.add_done_callback(self._log_exception)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    def _log_exception(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        self._logger.error("Task exited with error", extra={"task_name": self._name, "error": repr(task.exception())})


class TaskBag:
    """管理一组并发运行的 asyncio.Task,自动 GC 已完成项,提供统一 drain 出口。

    与 BackgroundTask 的区别:BackgroundTask 管单个长生命周期 loop(ws_event_loop、
    scheduler_loop),TaskBag 管多份"提交后即返回"的后台任务(视频轮询、cron job spawn)。
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._tasks: set[asyncio.Task] = set()

    def add(self, task: asyncio.Task, *, on_error: Callable[[asyncio.Task], None] | None = None) -> None:
        """注册 task;完成时自动从集合移除;若带异常且提供了 on_error 则触发。"""
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._on_done(t, on_error))

    def _on_done(self, task: asyncio.Task, on_error: Callable[[asyncio.Task], None] | None) -> None:
        self._tasks.discard(task)
        if on_error is None or task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            on_error(task)

    async def drain(self) -> None:
        """取消所有未完成 task 并等待其 settle。"""
        pending = list(self._tasks)
        for t in pending:
            if not t.done():
                t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def __len__(self) -> int:
        return len(self._tasks)
