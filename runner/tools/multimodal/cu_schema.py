from typing import Any

from utils import IS_WINDOWS

# Win 真实键鼠；mac 后台投递且不画编号。
if IS_WINDOWS:
    _INPUT_BEHAVIOR = (
        "Input uses the real mouse and keyboard: clicks land on whatever is visible at that screen position, and "
        "type/key are refused unless the target window is in the foreground. Raise it first with focus_app and "
        "bring_to_front=true when it is covered or typing is needed; this interrupts the user."
    )
    _SOM_IMAGE = "the screenshot has the element numbers drawn on it"
    _BRING_TO_FRONT = (
        "For focus_app: raise and focus the window so clicks and keystrokes reach it. This interrupts the user; "
        "use only when needed."
    )
    _KEY_EXAMPLES = "'ctrl+s', 'ctrl+shift+t', 'alt+tab', 'enter', 'escape'"
    _DRAG_SUMMARY = ", scroll and drag"
    _POINTER_ACTIONS = "click, drag or scroll"
else:
    _INPUT_BEHAVIOR = (
        "Input is delivered to the target window in the background when the application supports it; actions "
        "that need the window in the foreground (such as clicks with modifier keys) may be refused."
    )
    _SOM_IMAGE = "the screenshot itself has no numbers drawn on it"
    _BRING_TO_FRONT = "Raising windows is not supported on this computer; input is sent without raising the window."
    _KEY_EXAMPLES = "'cmd+s', 'cmd+shift+t', 'return', 'escape', 'tab'"
    _DRAG_SUMMARY = " and scroll (dragging is not available)"
    _POINTER_ACTIONS = "click or scroll"

_COORDINATE = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}

_ACTIONS = [
    "capture",
    "click",
    "double_click",
    "right_click",
    "middle_click",
    "drag",
    "scroll",
    "type",
    "key",
    "set_value",
    "wait",
    "list_apps",
    "focus_app",
]

# mac 仅前台拖拽，故不提供 drag。
_DRAG_PARAMS: dict[str, Any] = (
    {
        "from_element": {"type": "integer", "description": "Drag start element index."},
        "to_element": {"type": "integer", "description": "Drag end element index."},
        "from_coordinate": {**_COORDINATE, "description": "Drag start [x, y] in capture-image pixels."},
        "to_coordinate": {**_COORDINATE, "description": "Drag end [x, y] in capture-image pixels."},
    }
    if IS_WINDOWS
    else {}
)

COMPUTER_USE_SCHEMA: dict[str, Any] = {
    "name": "computer_use",
    "description": (
        f"Inspect and operate desktop applications with screenshots, mouse, keyboard{_DRAG_SUMMARY}. "
        "Start with action='capture' (optionally with app) to choose the target window and list its elements, "
        "then act by element index; use pixel coordinates from the capture image only when no element fits. "
        "Input actions apply to the window chosen by the latest capture or focus_app. "
        f"{_INPUT_BEHAVIOR} "
        "ok=true means the input was sent, not that the application reacted as intended; verify with a fresh "
        "capture. Hidden or minimized windows may be inaccessible."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": _ACTIONS if IS_WINDOWS else [a for a in _ACTIONS if a != "drag"],
                "description": (
                    "Action to perform. capture and list_apps only inspect state. Use set_value for dropdowns, "
                    "sliders and text fields when the element supports it."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["som", "vision", "ax"],
                "description": (
                    "Capture mode. som (default): screenshot plus the numbered list of interactable elements; "
                    f"{_SOM_IMAGE}. vision: screenshot only. ax: element list only, no image."
                ),
            },
            "app": {
                "type": "string",
                "description": (
                    "For capture or focus_app: the application to target, matched against the names from list_apps "
                    "(substring, case-insensitive; macOS may use localized names). Without app, capture targets the "
                    "frontmost window. 'desktop' (or 'screen', 'fullscreen', 'all') targets the desktop and the "
                    "taskbar or Dock. Other actions ignore this field."
                ),
            },
            "max_elements": {
                "type": "integer",
                "description": (
                    "Maximum number of elements listed by capture. When more exist, the result reports how many were "
                    "omitted; narrow with app or raise this limit."
                ),
                "default": 100,
                "minimum": 1,
                "maximum": 1000,
            },
            "element": {
                "type": "integer",
                "description": "Element index from the latest capture of the target window. Preferred over coordinates.",
            },
            "coordinate": {
                **_COORDINATE,
                "description": "[x, y] pixel position in the latest capture image (top-left origin).",
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button. Defaults to left.",
            },
            "modifiers": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["cmd", "ctrl", "shift", "option", "alt", "win", "fn"],
                },
                "description": f"Modifier keys held during a {_POINTER_ACTIONS}.",
            },
            **_DRAG_PARAMS,
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction.",
            },
            "amount": {"type": "integer", "description": "Scroll wheel notches, 1-50. Default 3."},
            "value": {
                "type": "string",
                "description": (
                    "For set_value: the value to set. For dropdowns pass the option's visible label; for sliders "
                    "pass the number."
                ),
            },
            "text": {"type": "string", "description": "Text to type into the target window."},
            "keys": {
                "type": "string",
                "description": f"Key combo joined with '+', e.g. {_KEY_EXAMPLES}.",
            },
            "seconds": {"type": "number", "description": "Seconds to wait, at most 30."},
            "capture_after": {
                "type": "boolean",
                "description": "If true and the action succeeds, capture the target window again and include it.",
            },
            "bring_to_front": {"type": "boolean", "description": _BRING_TO_FRONT},
        },
        "required": ["action"],
    },
}
