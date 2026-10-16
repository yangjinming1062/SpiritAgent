import logging
from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.desktop.activity import (
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
    "description": (
        "Take one point-in-time probe and return {idle_seconds: number}. The value is seconds since the last user "
        "input; -1 means the probe was unavailable. Use it when a current reading is needed; do not poll it in an "
        "unbounded loop."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_IS_LOCKED_SCHEMA = {
    "name": "system.is_screen_locked",
    "description": (
        "Take one point-in-time probe and return {locked: boolean}. True means the workstation session is detected "
        "as locked. False means unlocked or unavailable detection; it does not by itself prove the desktop is "
        "available or that the user welcomes contact."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_FOCUS_SCHEMA = {
    "name": "system.get_focused_app",
    "description": (
        "Take one point-in-time probe and return {focused_app: {name, pid, title or bundle, window_id, x, y, w, h}}; "
        "window fields are omitted when unknown, and focused_app={} means the foreground app could not be "
        "determined."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_IS_FULLSCREEN_SCHEMA = {
    "name": "system.is_fullscreen",
    "description": (
        "Take one point-in-time probe and return {fullscreen: boolean}. True when the foreground window covers its "
        "entire display, as fullscreen video, games and presentations do; maximized windows that leave the taskbar "
        "or menu bar visible are not fullscreen. False also covers unknown detection."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_SNAPSHOT_SCHEMA = {
    "name": "system.snapshot",
    "description": (
        "Take one point-in-time probe and return {idle_seconds, locked, focused_app, fullscreen} in one call — the "
        "same data as the four individual system.* probes. Fallbacks match those probes: idle_seconds=-1, "
        "focused_app={}, and locked/fullscreen=false when unavailable. Use this call when all four signals are "
        "needed; do not loop without a bound."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_POWER_SCHEMA = {
    "name": "system.get_power_state",
    "description": (
        "Return {on_battery: boolean, charging: boolean}; both are false when there is no battery or the power "
        "state is unknown."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_GET_WINDOWS_SCHEMA = {
    "name": "system.get_windows",
    "description": (
        "Return visible top-level windows front to back as {windows: [{title, name, x, y, w, h, focused, visible, "
        "window_id, pid, z_order}, ...]}; runner_instance_id may identify the current Runner process. Minimized, "
        "hidden and cloaked windows are excluded. x/y/w/h are global screen coordinates, the same space as "
        "system.get_cursor_pos and system.click_at."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_OPEN_APP_SCHEMA = {
    "name": "system.open_application",
    "description": (
        "Open an application by name (e.g. 'chrome', 'notepad', 'Calculator') or executable path. Returns "
        "{opened: true, name} or {opened: false, error}; opened=true means the launch request was accepted, not "
        "that the application is ready or focused."
    ),
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Application name or executable path"}},
        "required": ["name"],
        "additionalProperties": False,
    },
}

SYSTEM_GET_WORK_AREA_SCHEMA = {
    "name": "system.get_work_area",
    "description": "Primary display's working area {x, y, w, h} excluding the taskbar or Dock, in global screen coordinates.",
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_GET_CURSOR_POS_SCHEMA = {
    "name": "system.get_cursor_pos",
    "description": "Current mouse pointer position {x, y} in global screen coordinates.",
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}

SYSTEM_CLICK_AT_SCHEMA = {
    "name": "system.click_at",
    "description": (
        "Click the real mouse at global screen coordinates (x, y), the same space as system.get_windows. This moves "
        "the user's pointer. Returns {clicked: true, x, y, button, clicks} or {clicked: false, error}; a successful "
        "click confirms the input was dispatched, not that the target application consumed it. After a failure the "
        "pointer may already have moved, so check the current state before retrying."
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
        "additionalProperties": False,
    },
}


def _idle_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"idle_seconds": get_idle_seconds()}


def _locked_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"locked": is_screen_locked()}


def _focus_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"focused_app": get_focused_app()}


def _fullscreen_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"fullscreen": is_fullscreen()}


def _snapshot_handler(args: dict[str, Any]) -> dict[str, Any]:
    # 单项失败回落默认值。
    return {
        "idle_seconds": get_idle_seconds(),
        "locked": is_screen_locked(),
        "focused_app": get_focused_app(),
        "fullscreen": is_fullscreen(),
    }


def _power_handler(args: dict[str, Any]) -> dict[str, Any]:
    return get_power_state()


def _windows_handler(args: dict[str, Any]) -> dict[str, Any]:
    return get_windows().model_dump(mode="json", exclude_none=True)


def _open_app_handler(args: dict[str, Any]) -> dict[str, Any]:
    return open_application(str(args.get("name", "")))


def _work_area_handler(args: dict[str, Any]) -> dict[str, Any]:
    return get_work_area()


def _cursor_pos_handler(args: dict[str, Any]) -> dict[str, Any]:
    return get_cursor_pos()


def _click_at_handler(args: dict[str, Any]) -> dict[str, Any]:
    try:
        x, y, clicks = int(args["x"]), int(args["y"]), int(args.get("clicks", 1))
    except (KeyError, TypeError, ValueError) as e:
        return {"clicked": False, "error": f"x and y are required integers: {e}"}
    if (button := str(args.get("button") or "left")) not in {"left", "right", "middle"}:
        return {"clicked": False, "error": f"bad button {button!r}; use left, right or middle"}
    if not 1 <= clicks <= 3:
        return {"clicked": False, "error": "clicks must be between 1 and 3"}
    return click_at(x, y, button, clicks)


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="system.get_idle_seconds",
            toolset_id="system_awareness",
            schema=SYSTEM_GET_IDLE_SCHEMA,
            handler=lambda _context, args: _idle_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.is_screen_locked",
            toolset_id="system_awareness",
            schema=SYSTEM_IS_LOCKED_SCHEMA,
            handler=lambda _context, args: _locked_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.get_focused_app",
            toolset_id="system_awareness",
            schema=SYSTEM_FOCUS_SCHEMA,
            handler=lambda _context, args: _focus_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.is_fullscreen",
            toolset_id="system_awareness",
            schema=SYSTEM_IS_FULLSCREEN_SCHEMA,
            handler=lambda _context, args: _fullscreen_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.snapshot",
            toolset_id="system_awareness",
            schema=SYSTEM_SNAPSHOT_SCHEMA,
            handler=lambda _context, args: _snapshot_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.get_power_state",
            toolset_id="system_awareness",
            schema=SYSTEM_POWER_SCHEMA,
            handler=lambda _context, args: _power_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.get_windows",
            toolset_id="system_awareness",
            schema=SYSTEM_GET_WINDOWS_SCHEMA,
            handler=lambda _context, args: _windows_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.open_application",
            toolset_id="system_awareness",
            schema=SYSTEM_OPEN_APP_SCHEMA,
            handler=lambda _context, args: _open_app_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.get_work_area",
            toolset_id="system_awareness",
            schema=SYSTEM_GET_WORK_AREA_SCHEMA,
            handler=lambda _context, args: _work_area_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.get_cursor_pos",
            toolset_id="system_awareness",
            schema=SYSTEM_GET_CURSOR_POS_SCHEMA,
            handler=lambda _context, args: _cursor_pos_handler(args),
            check_fn=None,
            is_async=False,
        ),
        ToolDefinition(
            name="system.click_at",
            toolset_id="system_awareness",
            schema=SYSTEM_CLICK_AT_SCHEMA,
            handler=lambda _context, args: _click_at_handler(args),
            check_fn=None,
            is_async=False,
        ),
    ]
