"""夜间调度、阶段与动作共用的本地时间窗口。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from components import SETTINGS


def in_nightly_window(now: datetime, timezone: ZoneInfo) -> bool:
    hour = now.astimezone(timezone).hour
    start = SETTINGS.nightly_window_start_hour
    end = SETTINGS.nightly_window_end_hour
    if start < end:
        return start <= hour < end
    return start != end and (hour >= start or hour < end)
