"""进程内后台任务归属与收敛；业务持久状态由任务所属模块管理。"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from infrastructure.async_io import cancel_tasks, wait_for_tasks


@dataclass
class TaskOwnership:
    owner: str
    user_id: int | None = None
    cancel_on_maintenance: bool = True
    on_error: Callable[[asyncio.Task[Any]], None] | None = None


class TaskSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[asyncio.Task[Any], TaskOwnership] = {}
        self._logger = logging.getLogger(__name__)

    def register(
        self,
        task: asyncio.Task[Any],
        *,
        owner: str | None = None,
        user_id: int | None = None,
        cancel_on_maintenance: bool = True,
        on_error: Callable[[asyncio.Task[Any]], None] | None = None,
    ) -> None:
        ownership = self._tasks.get(task)
        if ownership is None:
            ownership = TaskOwnership(owner=owner or task.get_name())
            self._tasks[task] = ownership
            task.add_done_callback(self._finished)
        elif owner is not None:
            ownership.owner = owner
        if user_id is not None:
            if ownership.user_id is not None and ownership.user_id != user_id:
                raise ValueError("a background task cannot belong to multiple accounts")
            ownership.user_id = user_id
            ownership.cancel_on_maintenance = cancel_on_maintenance
        if on_error is not None:
            ownership.on_error = on_error

    def _finished(self, task: asyncio.Task[Any]) -> None:
        ownership = self._tasks.pop(task, None)
        if ownership is None or task.cancelled() or (error := task.exception()) is None:
            return
        if ownership.on_error is not None:
            ownership.on_error(task)
            return
        self._logger.error(
            "background task failed",
            extra={"owner": ownership.owner, "user_id": ownership.user_id, "task_name": task.get_name()},
            exc_info=error,
        )

    def tasks(self, *, owner: str | None = None, user_id: int | None = None) -> tuple[asyncio.Task[Any], ...]:
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        return tuple(
            task
            for task, ownership in self._tasks.items()
            if task is not current
            and not task.done()
            and (owner is None or ownership.owner == owner)
            and (user_id is None or ownership.user_id == user_id)
        )

    def task_count(self, owner: str) -> int:
        return sum(ownership.owner == owner and not task.done() for task, ownership in self._tasks.items())

    async def quiesce_user(self, user_id: int) -> int:
        """取消可中断任务，等待已提交付费工作及其派生任务落地。"""
        return await self._drain(user_id=user_id, respect_maintenance_policy=True)

    async def _drain(
        self,
        *,
        owner: str | None = None,
        user_id: int | None = None,
        respect_maintenance_policy: bool = False,
    ) -> int:
        cancelled = 0
        interrupted = False
        while pending := self.tasks(owner=owner, user_id=user_id):
            cancelled += cancel_tasks(
                task for task in pending if not respect_maintenance_policy or self._tasks[task].cancel_on_maintenance
            )
            try:
                await wait_for_tasks(pending)
            except asyncio.CancelledError:
                interrupted = True
        if interrupted:
            raise asyncio.CancelledError
        return cancelled

    async def drain(self, *, owner: str | None = None) -> None:
        """逐轮取消并等待，覆盖任务收尾期间派生的新工作。"""
        await self._drain(owner=owner)


TASK_SUPERVISOR = TaskSupervisor()
