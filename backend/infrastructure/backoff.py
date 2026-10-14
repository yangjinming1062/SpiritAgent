"""提供商轮询回路的退避工具。"""

import random


def backoff_for_poll(attempt: int, *, base_interval: float, max_interval: float) -> float:
    """带等比抖动的指数退避，达到最大间隔后持续查询原任务。"""
    base = min(base_interval * (2 ** min(attempt, 30)), max_interval)
    return base / 2 + random.uniform(0, base / 2)
