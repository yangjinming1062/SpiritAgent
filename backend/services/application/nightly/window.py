"""夜间调度、阶段与动作共用的本地时间窗口。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from components import SETTINGS, utc_now

from services.infrastructure.llm import LlmCallBlockedError


class NightlyWindowClosedError(LlmCallBlockedError):
    """停止新增模型请求，不持久化为模型的主动跳过决定。"""


async def require_nightly_window(timezone: ZoneInfo) -> None:
    if not in_nightly_window(utc_now(), timezone):
        raise NightlyWindowClosedError("夜间窗口已结束")


def in_nightly_window(now: datetime, timezone: ZoneInfo) -> bool:
    hour = now.astimezone(timezone).hour
    start = SETTINGS.nightly_window_start_hour
    end = SETTINGS.nightly_window_end_hour
    if start < end:
        return start <= hour < end
    return start != end and (hour >= start or hour < end)
