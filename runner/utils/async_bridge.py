import asyncio
import concurrent.futures
import logging
from collections.abc import Coroutine
from typing import Any


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
