import asyncio
from collections.abc import Callable

from infrastructure.logger import get_logger

logger = get_logger(__name__)


async def run_blocking[**P, T](operation: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """取消时等待线程退出，再交还调用方持有的锁、文件和临时目录。"""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        settled = asyncio.gather(task, return_exceptions=True)
        while not settled.done():
            try:
                await asyncio.shield(settled)
            except asyncio.CancelledError:
                continue
        result = settled.result()[0]
        if isinstance(result, Exception):
            logger.warning("Blocking operation failed during cancellation", exc_info=result)
        raise
