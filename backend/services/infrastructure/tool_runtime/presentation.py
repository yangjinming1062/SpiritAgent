"""呈现模式对应的动作工具族；调用方提供已验证的当前模式。"""

from modules.companion import PresentationMode

WINDOW_ACTION_TOOL_NAMES = frozenset({"action_search", "action_design", "action_inspect", "action_play"})
DESKTOP_ACTION_TOOL_NAMES = frozenset(
    {"desktop_action_search", "desktop_action_design", "desktop_action_inspect", "desktop_action_play"},
)


def unavailable_presentation_tool_names(mode: PresentationMode | None) -> frozenset[str]:
    return WINDOW_ACTION_TOOL_NAMES if mode == "desktop" else DESKTOP_ACTION_TOOL_NAMES
