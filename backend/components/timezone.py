from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_timezone(value: object) -> ZoneInfo | None:
    """解析 IANA 时区；缺失或非法返回 None，由调用方选择 UTC 回退或跳过。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, OSError, ValueError):
        return None
