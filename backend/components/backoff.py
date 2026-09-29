"""提供商轮询回路的退避工具。"""

import random


def backoff_for_poll(attempt: int, *, base_interval: float, max_interval: float, remaining_seconds: float) -> float:
    """带等比抖动的指数退避间隔，不超过剩余超时预算；预算耗尽返回 0。"""
    base = min(base_interval * (2**attempt), max_interval)
    return max(0.0, min(base / 2 + random.uniform(0, base / 2), remaining_seconds))
