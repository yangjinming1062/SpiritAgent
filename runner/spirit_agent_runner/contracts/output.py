from typing import Any

from spirit_agent_runner.contracts.config import load_config

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_LINE_LENGTH = 2000


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        return iv if (iv := int(value)) > 0 else default
    except (TypeError, ValueError):
        return default


def get_tool_output_limits() -> dict[str, int]:
    if not isinstance(section := load_config().get("tool_output"), dict):
        section = {}

    return {
        "max_lines": _coerce_positive_int(section.get("max_lines"), DEFAULT_MAX_LINES),
        "max_line_length": _coerce_positive_int(section.get("max_line_length"), DEFAULT_MAX_LINE_LENGTH),
    }


def get_max_lines() -> int:
    return get_tool_output_limits()["max_lines"]


def get_max_line_length() -> int:
    return get_tool_output_limits()["max_line_length"]
