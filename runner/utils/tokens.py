"""线程与 asyncio 协同的取消标记及竞争辅助。"""

import asyncio
import contextlib
import threading
from collections.abc import Awaitable
from typing import Any


class CancellationToken:
    """线程安全的取消标记，支持跨线程触发与 asyncio 异步等待。"""

    def __init__(self) -> None:
        self._t = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._a: asyncio.Event | None = None
        self._a_lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定主事件循环，供跨线程 ``set()`` 派发。"""
        self._loop = loop

    def _get_aio_event(self) -> asyncio.Event:
        with self._a_lock:
            if self._a is None:
                ev = asyncio.Event()
                if self._t.is_set():
                    ev.set()
                self._a = ev
            return self._a

    def set(self) -> None:
        """设置取消标记；跨线程设置时安全派发至事件循环。"""
        self._t.set()
        with self._a_lock:
            a = self._a
        if a is None:
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(a.set)
                return
        with contextlib.suppress(Exception):
            a.set()

    def is_set(self) -> bool:
        return self._t.is_set()

    async def wait(self) -> None:
        """等待取消触发，仅供内部竞争使用。"""
        await self._get_aio_event().wait()


async def check_cancel(token: CancellationToken | None) -> None:
    """非阻塞异步检查；已取消则抛出 ``CancelledError``。"""
    if token is not None and token.is_set():
        raise asyncio.CancelledError()


def raise_if_cancelled(token: CancellationToken | None) -> None:
    """同步非阻塞检查，供工作线程内的同步工具调用。"""
    if token is not None and token.is_set():
        raise asyncio.CancelledError()


async def race_cancel(work: Awaitable, token: CancellationToken | None) -> Any:
    """让任意 awaitable 与取消标记竞争；token 未指定时直通，已取消时立即中断。"""
    if token is None:
        return await work
    if token.is_set():
        if hasattr(work, "close") and callable(work.close):
            with contextlib.suppress(Exception):
                work.close()
        raise asyncio.CancelledError()
    work_task = asyncio.ensure_future(work)
    token_task = asyncio.ensure_future(token.wait())
    try:
        done, _ = await asyncio.wait({work_task, token_task}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        for t in (work_task, token_task):
            if not t.done():
                t.cancel()
        raise
    if work_task in done:
        if not token_task.done():
            token_task.cancel()
        return work_task.result()
    if not work_task.done():
        work_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await work_task
    raise asyncio.CancelledError()
