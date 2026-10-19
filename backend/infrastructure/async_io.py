import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from infrastructure.logger import get_logger

logger = get_logger(__name__)


async def settle_task[T](task: asyncio.Future[T]) -> T:
    """清理期间等目标任务退出；等待方重复取消不能提前释放目标仍持有的资源。"""
    settled = asyncio.gather(task, return_exceptions=True)
    while not settled.done():
        try:
            await asyncio.shield(settled)
        except asyncio.CancelledError:
            continue
    return task.result()


def cancel_tasks(tasks: Iterable[asyncio.Task[Any] | None], *, message: str | None = None) -> int:
    """只请求一次取消，排除当前任务；返回本次新请求取消的数量。"""
    cancelled = 0
    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    for task in tasks:
        if task is not None and task is not current and not task.done() and not task.cancelling():
            task.cancel(message)
            cancelled += 1
    return cancelled


async def wait_for_task[T](task: asyncio.Future[T]) -> T:
    """等待方取消不传给目标任务；等目标释放资源后再传播取消。"""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await settle_task(asyncio.gather(task, return_exceptions=True))
        raise


async def wait_for_tasks[T](tasks: Iterable[asyncio.Future[T]]) -> list[T | BaseException]:
    """等待整批任务收尾并收集结果；等待方取消遵循单任务的资源释放保证。"""
    targets = tuple(tasks)
    return await wait_for_task(asyncio.gather(*targets, return_exceptions=True)) if targets else []


async def cancel_and_wait(tasks: Iterable[asyncio.Task[Any] | None], *, message: str | None = None) -> int:
    """取消并收敛一批任务；重复取消等待方时仍等到任务释放资源。"""
    current = asyncio.current_task()
    targets = tuple(dict.fromkeys(task for task in tasks if task is not None and task is not current))
    cancelled = cancel_tasks(targets, message=message)
    await wait_for_tasks(targets)
    return cancelled


async def run_blocking[**P, T](operation: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """取消时等待线程退出，再交还调用方持有的锁、文件和临时目录。"""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await wait_for_task(task)
    except asyncio.CancelledError:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning("Blocking operation failed during cancellation", exc_info=error)
        raise
