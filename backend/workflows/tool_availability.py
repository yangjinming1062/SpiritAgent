"""按本轮冻结影像视图决定可披露的伙伴影像工具。"""

from runtime.tools.presentation import VISUAL_TOOL_NAMES

from workflows.companion_actions import ActionViewSnapshot, build_action_view


async def unavailable_action_tool_names(
    user_id: int,
    *,
    action_view: ActionViewSnapshot | None = None,
) -> frozenset[str]:
    view = action_view if action_view is not None else await build_action_view(user_id)
    return VISUAL_TOOL_NAMES - view.available_tool_names
