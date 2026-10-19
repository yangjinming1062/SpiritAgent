import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from infrastructure.async_io import cancel_and_wait

from .task_supervisor import TASK_SUPERVISOR


class BackgroundTask:
    """封装单个长生命周期 asyncio.Task 与 done 回调；统一各后台循环（scheduler、ws_event_loop）的 start/stop 形态。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self._task: asyncio.Task | None = None
        self._logger = logging.getLogger(name)

    def start(self, coro: Coroutine[Any, Any, Any]) -> None:
        if self._task and not self._task.done():
            coro.close()
            return
        self._task = asyncio.create_task(coro, name=self._name)
        TASK_SUPERVISOR.register(self._task, owner=self._name, on_error=self._log_exception)

    async def stop(self) -> None:
        task = self._task
        try:
            if task is not None and not task.done():
                await cancel_and_wait((task,))
                if not task.cancelled():
                    task.result()
        finally:
            self._task = None

    def _log_exception(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        self._logger.error("Task exited with error", extra={"task_name": self._name}, exc_info=task.exception())


class TaskBag:
    """管理一组并发 asyncio.Task 并提供统一 drain；管「提交后即返回」的后台任务（视频轮询、cron spawn），单 loop 归 BackgroundTask。"""

    def __init__(self, name: str) -> None:
        self._name = name

    def add(self, task: asyncio.Task, *, on_error: Callable[[asyncio.Task], None] | None = None) -> None:
        """注册 task;完成时自动从集合移除;若带异常且提供了 on_error 则触发。"""
        TASK_SUPERVISOR.register(task, owner=self._name, on_error=on_error)

    async def drain(self) -> None:
        """取消所有未完成 task 并等待其 settle。"""
        await TASK_SUPERVISOR.drain(owner=self._name)

    def __len__(self) -> int:
        return TASK_SUPERVISOR.task_count(self._name)
