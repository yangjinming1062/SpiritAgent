"""提供商轮询回路的退避工具。"""

import random


def backoff_for_poll(
    attempt: int,
    *,
    base_interval: float,
    max_interval: float,
    remaining_seconds: float | None = None,
    jitter: bool = True,
) -> float:
    """计算带等比抖动的轮询退避间隔，不超过剩余超时时间预算。"""
    if attempt < 0:
        attempt = 0
    base = min(base_interval * (2**attempt), max_interval)
    if jitter:
        half = base / 2
        sleep = half + random.uniform(0, half)
    else:
        sleep = base
    if remaining_seconds is None:
        return sleep
    if remaining_seconds <= 0:
        return 0.0
    return min(sleep, remaining_seconds)
