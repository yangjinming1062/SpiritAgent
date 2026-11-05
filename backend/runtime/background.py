"""进程内后台任务容器：单个常驻循环与 owner 作用域的任务集合；业务持久状态由任务所属模块管理。"""

import asyncio
import logging
from collections.abc import Callable, Coroutine, Hashable, Iterator
from typing import Any

from infrastructure.async_io import cancel_and_wait, wait_for_tasks

from .task_supervisor import TASK_SUPERVISOR
from .user_maintenance import track_user_task


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


class TaskScope[Key: Hashable = Hashable]:
    """owner 作用域内的后台任务集合：统一登记、按 key 单飞、错误回调与停机收敛。

    ``add`` 接收调用方已创建的 task；``spawn`` 以 key 保证同键在途时不重复启动，结束后释放。
    任务调度、持久状态与续跑语义仍归各业务模块。
    """

    def __init__(self, owner: str) -> None:
        self._owner = owner
        self._tasks: dict[Key, asyncio.Task[None]] = {}

    def add(
        self,
        task: asyncio.Task[Any],
        *,
        user_id: int | None = None,
        cancel_on_maintenance: bool = True,
        on_error: Callable[[asyncio.Task], None] | None = None,
    ) -> None:
        """登记模块与可选账户归属；账户任务同时遵循维护取消策略。"""
        if user_id is None:
            TASK_SUPERVISOR.register(task, owner=self._owner, on_error=on_error)
        else:
            track_user_task(
                user_id, task, owner=self._owner, cancel_on_maintenance=cancel_on_maintenance, on_error=on_error
            )

    def running(self, key: Key) -> asyncio.Task[None] | None:
        task = self._tasks.get(key)
        return task if task is not None and not task.done() else None

    def __iter__(self) -> Iterator[Key]:
        """按登记顺序遍历当前的单飞 key；调用方自行过滤。"""
        return iter(self._tasks)

    def spawn(
        self,
        key: Key,
        factory: Callable[[], Coroutine[Any, Any, None]],
        *,
        name: str | None = None,
        user_id: int | None = None,
        cancel_on_maintenance: bool = True,
        on_error: Callable[[asyncio.Task], None] | None = None,
        on_done: Callable[[asyncio.Task], None] | None = None,
    ) -> bool:
        """同 key 在途时不重复启动并返回是否本次启动；完成回调在释放 key 后执行，包括取消路径。"""
        if self.running(key) is not None:
            return False
        task = asyncio.create_task(factory(), name=name or f"{self._owner}:{key}")
        self._tasks[key] = task
        self.add(task, user_id=user_id, cancel_on_maintenance=cancel_on_maintenance, on_error=on_error)

        def finished(done: asyncio.Task[None]) -> None:
            if self._tasks.get(key) is done:
                self._tasks.pop(key, None)
            if on_done is not None:
                on_done(done)

        task.add_done_callback(finished)
        return True

    async def wait(self, key: Key) -> None:
        task = self.running(key)
        if task is not None:
            await wait_for_tasks((task,))

    async def cancel_and_wait(self, key: Key) -> None:
        task = self.running(key)
        if task is not None:
            await cancel_and_wait((task,))

    async def drain(self) -> None:
        """取消并等待本 owner 的全部任务收尾，覆盖收尾期间派生的新工作。"""
        await TASK_SUPERVISOR.drain(owner=self._owner)
