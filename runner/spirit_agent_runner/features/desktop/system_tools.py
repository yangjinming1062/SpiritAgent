from collections.abc import Callable
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

_NO_PARAMS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
_OPEN_APP_PARAMS = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "Application name or executable path"}},
    "required": ["name"],
    "additionalProperties": False,
}
_CLICK_AT_PARAMS = {
    "type": "object",
    "properties": {
        "x": {"type": "integer", "description": "Screen X coordinate"},
        "y": {"type": "integer", "description": "Screen Y coordinate"},
        "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
        "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
    },
    "required": ["x", "y"],
    "additionalProperties": False,
}


def _snapshot() -> dict[str, Any]:
    # 单项失败回落默认值，契约见各单项探针的 schema 描述。
    return {
        "idle_seconds": get_idle_seconds(),
        "locked": is_screen_locked(),
        "focused_app": get_focused_app(),
        "fullscreen": is_fullscreen(),
    }


def _open_app(args: dict[str, Any]) -> dict[str, Any]:
    return open_application(str(args.get("name", "")))


def _click_at(args: dict[str, Any]) -> dict[str, Any]:
    try:
        x, y, clicks = int(args["x"]), int(args["y"]), int(args.get("clicks", 1))
    except (KeyError, TypeError, ValueError) as e:
        return {"clicked": False, "error": f"x and y are required integers: {e}"}
    if (button := str(args.get("button") or "left")) not in {"left", "right", "middle"}:
        return {"clicked": False, "error": f"bad button {button!r}; use left, right or middle"}
    if not 1 <= clicks <= 3:
        return {"clicked": False, "error": "clicks must be between 1 and 3"}
    return click_at(x, y, button, clicks)


def _tool(
    name: str, description: str, handler: Callable[[Any, dict[str, Any]], Any], parameters: dict[str, Any] | None = None
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        toolset_id="system_awareness",
        schema={"name": name, "description": description, "parameters": parameters or _NO_PARAMS},
        handler=handler,
        is_async=False,
    )


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        _tool(
            "system.get_idle_seconds",
            "Take one point-in-time probe and return {idle_seconds: number}. The value is seconds since the last user "
            "input; -1 means the probe was unavailable. Use it when a current reading is needed; do not poll it in an "
            "unbounded loop.",
            lambda _ctx, _args: {"idle_seconds": get_idle_seconds()},
        ),
        _tool(
            "system.is_screen_locked",
            "Take one point-in-time probe and return {locked: boolean}. True means the workstation session is detected "
            "as locked. False means unlocked or unavailable detection; it does not by itself prove the desktop is "
            "available or that the user welcomes contact.",
            lambda _ctx, _args: {"locked": is_screen_locked()},
        ),
        _tool(
            "system.get_focused_app",
            "Take one point-in-time probe and return {focused_app: {name, pid, title or bundle, window_id, x, y, w, h}}; "
            "window fields are omitted when unknown, and focused_app={} means the foreground app could not be "
            "determined.",
            lambda _ctx, _args: {"focused_app": get_focused_app()},
        ),
        _tool(
            "system.is_fullscreen",
            "Take one point-in-time probe and return {fullscreen: boolean}. True when the foreground window covers its "
            "entire display, as fullscreen video, games and presentations do; maximized windows that leave the taskbar "
            "or menu bar visible are not fullscreen. False also covers unknown detection.",
            lambda _ctx, _args: {"fullscreen": is_fullscreen()},
        ),
        _tool(
            "system.snapshot",
            "Take one point-in-time probe and return {idle_seconds, locked, focused_app, fullscreen} in one call — the "
            "same data as the four individual system.* probes. Fallbacks match those probes: idle_seconds=-1, "
            "focused_app={}, and locked/fullscreen=false when unavailable. Use this call when all four signals are "
            "needed; do not loop without a bound.",
            lambda _ctx, _args: _snapshot(),
        ),
        _tool(
            "system.get_power_state",
            "Return {on_battery: boolean, charging: boolean}; both are false when there is no battery or the power "
            "state is unknown.",
            lambda _ctx, _args: get_power_state(),
        ),
        _tool(
            "system.get_windows",
            "Return visible top-level windows front to back as {windows: [{title, name, x, y, w, h, focused, visible, "
            "window_id, pid, z_order}, ...]}; runner_instance_id may identify the current Runner process. Minimized, "
            "hidden and cloaked windows are excluded. x/y/w/h are global screen coordinates, the same space as "
            "system.get_cursor_pos and system.click_at.",
            lambda _ctx, _args: get_windows().model_dump(mode="json", exclude_none=True),
        ),
        _tool(
            "system.open_application",
            "Open an application by name (e.g. 'chrome', 'notepad', 'Calculator') or executable path. Returns "
            "{opened: true, name} or {opened: false, error}; opened=true means the launch request was accepted, not "
            "that the application is ready or focused.",
            lambda _ctx, args: _open_app(args),
            _OPEN_APP_PARAMS,
        ),
        _tool(
            "system.get_work_area",
            "Primary display's working area {x, y, w, h} excluding the taskbar or Dock, in global screen coordinates.",
            lambda _ctx, _args: get_work_area(),
        ),
        _tool(
            "system.get_cursor_pos",
            "Current mouse pointer position {x, y} in global screen coordinates.",
            lambda _ctx, _args: get_cursor_pos(),
        ),
        _tool(
            "system.click_at",
            "Click the real mouse at global screen coordinates (x, y), the same space as system.get_windows. This moves "
            "the user's pointer. Returns {clicked: true, x, y, button, clicks} or {clicked: false, error}; a successful "
            "click confirms the input was dispatched, not that the target application consumed it. After a failure the "
            "pointer may already have moved, so check the current state before retrying.",
            lambda _ctx, args: _click_at(args),
            _CLICK_AT_PARAMS,
        ),
    ]
