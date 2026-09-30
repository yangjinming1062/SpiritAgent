import json
import logging
from typing import Any

from ..registry import registry
from .activity import (
    click_at,
    get_cursor_pos,
    get_focused_app,
    get_idle_seconds,
    get_power_state,
    get_windows,
    get_work_area,
    is_fullscreen,
    is_screen_locked,
    open_application,
)

logger = logging.getLogger(__name__)

SYSTEM_GET_IDLE_SCHEMA = {
    "name": "system.get_idle_seconds",
    "description": "Seconds since the last user input. Cheap; safe to poll.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_IS_LOCKED_SCHEMA = {
    "name": "system.is_screen_locked",
    "description": (
        "True when the workstation session is detected as locked. False means unlocked or unavailable detection; "
        "it does not by itself prove the desktop is available or that the user welcomes contact."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_FOCUS_SCHEMA = {
    "name": "system.get_focused_app",
    "description": (
        "The foreground app: {name, pid, title or bundle, window_id, x, y, w, h}; window fields are omitted when "
        "unknown, and {} means the foreground app could not be determined."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_IS_FULLSCREEN_SCHEMA = {
    "name": "system.is_fullscreen",
    "description": (
        "True when the foreground window covers its entire display, as fullscreen video, games and presentations "
        "do; maximized windows that leave the taskbar or menu bar visible are not fullscreen. False when unknown."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_SNAPSHOT_SCHEMA = {
    "name": "system.snapshot",
    "description": (
        "Aggregated activity snapshot: {idle_seconds, locked, focused_app, fullscreen} "
        "in one call — same data as the four individual system.* probes."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_POWER_SCHEMA = {
    "name": "system.get_power_state",
    "description": "{on_battery, charging}; both are false when there is no battery or the power state is unknown.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_GET_WINDOWS_SCHEMA = {
    "name": "system.get_windows",
    "description": (
        "Visible top-level windows, front to back: {windows: [{title, name, x, y, w, h, focused, window_id, pid, "
        "z_order}, ...]}. Minimized and hidden windows are excluded; x/y/w/h are global screen coordinates, the same "
        "space as system.get_cursor_pos and system.click_at."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_OPEN_APP_SCHEMA = {
    "name": "system.open_application",
    "description": (
        "Open an application by name (e.g. 'chrome', 'notepad', 'Calculator') or executable path. Returns "
        "{opened: true, name} or {opened: false, error}."
    ),
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Application name or executable path"}},
        "required": ["name"],
    },
}

SYSTEM_GET_WORK_AREA_SCHEMA = {
    "name": "system.get_work_area",
    "description": "Primary display's working area {x, y, w, h} excluding the taskbar or Dock, in global screen coordinates.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_GET_CURSOR_POS_SCHEMA = {
    "name": "system.get_cursor_pos",
    "description": "Current mouse pointer position {x, y} in global screen coordinates.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SYSTEM_CLICK_AT_SCHEMA = {
    "name": "system.click_at",
    "description": (
        "Click the real mouse at global screen coordinates (x, y), the same space as system.get_windows. This moves "
        "the user's pointer. Returns {clicked: true, ...} or {clicked: false, error}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Screen X coordinate"},
            "y": {"type": "integer", "description": "Screen Y coordinate"},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
        },
        "required": ["x", "y"],
    },
}


def _idle_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"idle_seconds": get_idle_seconds()})


def _locked_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"locked": is_screen_locked()})


def _focus_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"focused_app": get_focused_app()})


def _fullscreen_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps({"fullscreen": is_fullscreen()})


def _snapshot_handler(args: dict[str, Any], **kw: Any) -> str:
    # 单项失败回落默认值。
    return json.dumps(
        {
            "idle_seconds": get_idle_seconds(),
            "locked": is_screen_locked(),
            "focused_app": get_focused_app(),
            "fullscreen": is_fullscreen(),
        },
    )


def _power_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(get_power_state())


def _windows_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(get_windows())


def _open_app_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(open_application(str(args.get("name", ""))))


def _work_area_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(get_work_area())


def _cursor_pos_handler(args: dict[str, Any], **kw: Any) -> str:
    return json.dumps(get_cursor_pos())


def _click_at_handler(args: dict[str, Any], **kw: Any) -> str:
    try:
        x, y, clicks = int(args["x"]), int(args["y"]), int(args.get("clicks", 1))
    except (KeyError, TypeError, ValueError) as e:
        return json.dumps({"clicked": False, "error": f"x and y are required integers: {e}"}, ensure_ascii=False)
    if (button := str(args.get("button") or "left")) not in {"left", "right", "middle"}:
        return json.dumps({"clicked": False, "error": f"bad button {button!r}; use left, right or middle"})
    if not 1 <= clicks <= 3:
        return json.dumps({"clicked": False, "error": "clicks must be between 1 and 3"})
    return json.dumps(click_at(x, y, button, clicks))


registry.register_tool("system.get_idle_seconds", schema=SYSTEM_GET_IDLE_SCHEMA)(_idle_handler)
registry.register_tool("system.is_screen_locked", schema=SYSTEM_IS_LOCKED_SCHEMA)(_locked_handler)
registry.register_tool("system.get_focused_app", schema=SYSTEM_FOCUS_SCHEMA)(_focus_handler)
registry.register_tool("system.is_fullscreen", schema=SYSTEM_IS_FULLSCREEN_SCHEMA)(_fullscreen_handler)
registry.register_tool("system.snapshot", schema=SYSTEM_SNAPSHOT_SCHEMA)(_snapshot_handler)
registry.register_tool("system.get_power_state", schema=SYSTEM_POWER_SCHEMA)(_power_handler)
registry.register_tool("system.get_windows", schema=SYSTEM_GET_WINDOWS_SCHEMA)(_windows_handler)
registry.register_tool("system.open_application", schema=SYSTEM_OPEN_APP_SCHEMA)(_open_app_handler)
registry.register_tool("system.get_work_area", schema=SYSTEM_GET_WORK_AREA_SCHEMA)(_work_area_handler)
registry.register_tool("system.get_cursor_pos", schema=SYSTEM_GET_CURSOR_POS_SCHEMA)(_cursor_pos_handler)
registry.register_tool("system.click_at", schema=SYSTEM_CLICK_AT_SCHEMA)(_click_at_handler)
