"""定时循环入口；业务触发与状态由 scheduling 流程管理。"""

import asyncio

from infrastructure.api_ids import ResourceId
from infrastructure.config import SETTINGS
from infrastructure.correlation import begin_local_scope
from infrastructure.logger import get_logger
from runtime.background import BackgroundTask
from workflows import scheduling

logger = get_logger(__name__)
_SCHEDULER = BackgroundTask("scheduler.cron_loop")


async def drain() -> None:
    await scheduling.drain()


def invalidate_user_scheduler_state(user_id: ResourceId) -> None:
    scheduling.invalidate_user_scheduler_state(user_id)


async def scheduler_loop() -> None:
    """以 SETTINGS.scheduler_interval_seconds 为周期的 cron tick 循环（每轮重读，热调即时生效）：单 tick 粒度——不支持分钟以下调度。按 deadline 对齐而非 tick 结束后固定 sleep——后者的实际周期是 tick 间隔 + tick 耗时，误差逐轮累积；落后超过一整周期时丢弃错过的槽位，避免停摆恢复后连打。_tick() 未捕获的异常会冒泡导致 BackgroundTask 死亡，运维侧曝光度高（Task exited with error）——这是故意的：持久 bug 不该每个 tick 静默刷日志，而应显式崩溃以便修复；派发出去的自主 turn 与扫描各自在独立 task 里失败并落日志，不会终止循环。"""
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
