"""按本轮冻结动作视图决定可披露的动作工具。"""

from runtime.tools.presentation import ACTION_TOOL_NAMES
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.companion_actions import ActionViewSnapshot, build_action_view


async def unavailable_action_tool_names(
    db: AsyncSession,
    user_id: int,
    *,
    action_view: ActionViewSnapshot | None = None,
) -> frozenset[str]:
    view = action_view if action_view is not None else await build_action_view(db, user_id)
    return ACTION_TOOL_NAMES - view.available_tool_names
