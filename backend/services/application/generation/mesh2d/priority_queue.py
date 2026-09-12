"""LLM API 任务优先级调度 — 2d 切分任务按用户 render_mode 分高低优先级队列。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


@dataclass(order=True)
class _PrioritizedItem:
    priority: int
    seq: int
    future: asyncio.Future[None] = field(compare=False, repr=False)
    run: Callable[[], Awaitable[T]] = field(compare=False, repr=False)
    # 任务唯一 key（一般为 "2d:{user_id}"）；worker 用它清理 self._tasks。
    key: str = field(compare=False, repr=False, default="")


_SENTINEL_PRIORITY = 10_000


class PriorityTaskQueue:
    """高 / 低优先级调度：高优先级任务先跑；同优先级内按到达顺序。"""

    def __init__(self, *, high_workers: int = 2, low_workers: int = 1) -> None:
        self._high_workers = high_workers
        self._low_workers = low_workers
        self._queue: asyncio.PriorityQueue[_PrioritizedItem] = asyncio.PriorityQueue()
        self._seq = 0
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._lock = asyncio.Lock()
        self._tasks: dict[str, _PrioritizedItem] = {}

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                return

            self._running = True
            total = self._high_workers + self._low_workers

            for i in range(total):
                self._worker_tasks.append(asyncio.create_task(self._worker(i)))

    async def stop(self) -> None:
        async with self._lock:
            if not self._running:
                return

            self._running = False
            loop = asyncio.get_running_loop()

            for _ in range(len(self._worker_tasks)):
                await self._queue.put(
                    _PrioritizedItem(
                        priority=_SENTINEL_PRIORITY,
                        seq=self._next_seq(),
                        future=loop.create_future(),
                        run=_noop,
                    ),
                )

            for task in self._worker_tasks:
                await task

            self._worker_tasks.clear()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def submit(
        self,
        key: str,
        run: Callable[[], Awaitable[T]],
        *,
        priority: str,
    ) -> T:
        """提交任务；同一 key 的旧任务会被取消，新任务接力。"""
        prio = 0 if priority == "high" else 100

        if key in self._tasks:
            old = self._tasks.pop(key)
            old.future.cancel()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        seq = self._next_seq()
        item = _PrioritizedItem(priority=prio, seq=seq, future=future, run=run, key=key)
        self._tasks[key] = item

        await self._queue.put(item)

        if not self._running:
            await self.start()

        return await future

    async def _worker(self, idx: int) -> None:
        while True:
            item = await self._queue.get()

            try:
                if item.priority >= _SENTINEL_PRIORITY:
                    return

                if item.future.cancelled():
                    continue

                try:
                    await item.run()
                    if not item.future.done():
                        item.future.set_result(None)
                except Exception as exc:
                    if not item.future.done():
                        item.future.set_exception(exc)
            finally:
                if item.key and self._tasks.get(item.key) is item:
                    self._tasks.pop(item.key, None)


async def _noop() -> None:
    return None


_default_queue: PriorityTaskQueue | None = None


def get_default_queue() -> PriorityTaskQueue:
    global _default_queue

    if _default_queue is None:
        _default_queue = PriorityTaskQueue()

    return _default_queue
