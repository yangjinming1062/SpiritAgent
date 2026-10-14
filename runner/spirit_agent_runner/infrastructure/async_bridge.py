import asyncio
import concurrent.futures
import contextvars
import logging
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any


async def run_in_thread[T](
    function: Callable[..., T],
    /,
    *args: Any,
    on_cancel: Callable[[], None] | None = None,
    **kwargs: Any,
) -> T:
    """取消时等待系统调用线程结束，避免后台读写与资源清理竞争。"""
    context = contextvars.copy_context()
    worker = asyncio.get_running_loop().run_in_executor(None, partial(context.run, function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


def safe_schedule_threadsafe[T](
    coro: Coroutine[Any, Any, T],
    loop: asyncio.AbstractEventLoop | None,
    *,
    logger: logging.Logger | None = None,
    log_message: str = "safe_schedule_threadsafe: scheduling failed",
) -> concurrent.futures.Future[T] | None:
    """把协程调度到目标事件循环；循环缺失、已关闭或调度失败时关闭协程并返回 ``None``。"""
    if loop is not None and not loop.is_closed():
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError as exc:
            if logger is not None:
                logger.warning("%s: %s", log_message, exc)
    # 未调度的协程须显式关闭，否则回收时报 "coroutine was never awaited"。
    coro.close()
    return None
