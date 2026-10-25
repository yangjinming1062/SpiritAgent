"""定时循环入口；业务触发与状态由 scheduling 流程管理。"""

import asyncio

from infrastructure.config import SETTINGS
from infrastructure.correlation import begin_local_scope
from infrastructure.logger import get_logger
from runtime.background import BackgroundTask
from workflows import scheduling

logger = get_logger(__name__)
_SCHEDULER = BackgroundTask("scheduler.cron_loop")


async def scheduler_loop() -> None:
    """按动态间隔对齐 tick；跳过错过的时槽，未捕获错误由任务监督器报告。"""
    logger.info("Starting background cron scheduler loop.")
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    while True:
        begin_local_scope()
        await scheduling.run_tick()
        next_at += SETTINGS.scheduler_interval_seconds
        now = loop.time()
        if next_at <= now:
            next_at = now + SETTINGS.scheduler_interval_seconds
        await asyncio.sleep(next_at - now)


def start_scheduler() -> None:
    _SCHEDULER.start(scheduler_loop())


async def stop_scheduler() -> None:
    """取消 scheduler task 并等待其退出；await 防止晚到的 tick 在 dispatcher 已拆除后还触发自主 turn（那些 turn 会失去 emitter）。"""
    await _SCHEDULER.stop()
