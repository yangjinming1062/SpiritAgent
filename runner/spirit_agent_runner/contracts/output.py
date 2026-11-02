from typing import Any

from .config import load_config

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_LINE_LENGTH = 2000


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        return iv if (iv := int(value)) > 0 else default
    except (TypeError, ValueError):
        return default


def _tool_output(key: str, default: int) -> int:
    section = load_config().get("tool_output")
    return _coerce_positive_int(section.get(key) if isinstance(section, dict) else None, default)


def get_max_lines() -> int:
    return _tool_output("max_lines", DEFAULT_MAX_LINES)


def get_max_line_length() -> int:
    return _tool_output("max_line_length", DEFAULT_MAX_LINE_LENGTH)
