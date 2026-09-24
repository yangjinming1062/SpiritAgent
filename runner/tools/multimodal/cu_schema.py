from typing import Any

COMPUTER_USE_SCHEMA: dict[str, Any] = {
    "name": "computer_use",
    "description": (
        "Inspect and operate the desktop using screenshots, mouse, keyboard, scroll and drag. "
        "Background support depends on the platform, target application and action; "
        "keyboard or coordinate actions may affect the user's cursor and foreground app. Preferred workflow: call with "
        "action='capture' (mode='som' gives numbered element overlays), "
        "then click by `element` index for reliability. Pixel coordinates "
        "are available when an element cannot be used. Inspect the actual capture and action result; "
        "hidden or minimized windows may be inaccessible. "
        "Verify effects with a fresh capture; ok confirms the input request, not the intended application effect. "
        "macOS requires cua-driver; Windows uses built-in UIA automation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
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
                ],
                "description": (
                    "Which action to perform. capture and list_apps inspect state; other actions "
                    "operate within the user's authorized task. Use `set_value` for select/popup elements "
                    "and sliders — it selects the matching option directly "
                    "when the backend supports it."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["som", "vision", "ax"],
                "description": (
                    "Capture mode. `som` (default) is a screenshot with "
                    "numbered overlays on every interactable element plus "
                    "the AX tree — best for vision models, lets you click "
                    "by element index. `vision` is a plain screenshot. "
                    "`ax` is the accessibility tree only (no image; useful "
                    "for text-only models)."
                ),
            },
            "app": {
                "type": "string",
                "description": (
                    "For capture or focus_app, select a specific app "
                    "(by name, e.g. 'Safari', or bundle ID, "
                    "'com.apple.Safari'). If omitted, operates on the "
                    "frontmost app's window or the whole screen. For later input actions, use a fresh "
                    "capture or focus_app to establish the target; app alone does not retarget every action.\n"
                    "Sentinel values: 'screen' / 'desktop' / 'fullscreen' / "
                    "'all' resolve to the OS shell surface (Finder+Dock on "
                    "macOS, Progman+Shell_TrayWnd on Windows) so the agent "
                    "can capture the desktop background or taskbar."
                ),
            },
            "max_elements": {
                "type": "integer",
                "description": (
                    "Optional cap on the AX `elements` array returned by "
                    "`action='capture'`. Default 100, hard maximum 1000. "
                    "Dense UIs (Electron apps such as Obsidian or VS Code, "
                    "JetBrains IDEs) can publish 500+ AX nodes — capping "
                    "prevents a single capture from blowing session "
                    "context. When the cap trims the response, "
                    "`total_elements` and `truncated_elements` are "
                    "surfaced in the result so you can re-call with "
                    "`app=` to narrow scope or raise `max_elements` when "
                    "the full tree is required. Has no effect on "
                    "`mode='som'` / `mode='vision'` when a screenshot is "
                    "included in the response; only the rare image-"
                    "missing fallback returns an `elements` array and is "
                    "subject to the cap."
                ),
                "default": 100,
                "minimum": 1,
                "maximum": 1000,
            },
            "element": {
                "type": "integer",
                "description": (
                    "The 0-based SOM index returned by the last `capture(mode='som')` call. Strongly preferred over raw coordinates."
                ),
            },
            "coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": (
                    "Pixel coordinates [x, y] in logical screen space (as returned by capture width/height). Only use this if no element index is available."
                ),
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
                    "enum": [
                        "cmd",
                        "command",
                        "shift",
                        "option",
                        "alt",
                        "ctrl",
                        "fn",
                        "win",
                        "super",
                        "meta",
                        "windows",
                        "⌘",
                        "⌥",
                    ],
                },
                "description": "Modifier keys held during the action.",
            },
            "from_element": {"type": "integer", "description": "Source element index (drag)."},
            "to_element": {"type": "integer", "description": "Target element index (drag)."},
            "from_coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Source [x,y] (drag; use when no element available).",
            },
            "to_coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Target [x,y] (drag; use when no element available).",
            },
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction.",
            },
            "amount": {"type": "integer", "description": "Scroll wheel ticks. Default 3."},
            "value": {
                "type": "string",
                "description": (
                    "For action='set_value': the value to set on the element. "
                    "For AXPopUpButton / select dropdowns, pass the option's "
                    "display label (e.g. 'Blue'). For sliders and other "
                    "AXValue-settable elements, pass the numeric or string value."
                ),
            },
            "text": {"type": "string", "description": "Text to type (respects the current layout)."},
            "keys": {
                "type": "string",
                "description": (
                    "Key combo, e.g. 'cmd+s', 'ctrl+alt+t', 'return', 'escape', 'tab'. Use '+' to combine."
                ),
            },
            "seconds": {"type": "number", "description": "Seconds to wait. Max 30."},
            "capture_after": {
                "type": "boolean",
                "description": (
                    "If true, take a follow-up capture after the action and include it in the response. Saves a round-trip when you need to verify an action's effect."
                ),
            },
            "bring_to_front": {
                "type": "boolean",
                "description": (
                    "For focus_app only, request raising and focusing the window on Windows. "
                    "This may interrupt the user. The macOS backend cannot raise windows and reports "
                    "that limitation. Default false; other actions do not use this flag."
                ),
            },
        },
        "required": ["action"],
    },
}
