"""平台返回值的有界基础字段解析。"""

from datetime import UTC, datetime
from typing import Any


def text(value: Any, limit: int = 30000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, UTC)
    except (ValueError, OverflowError, OSError):
        return None
