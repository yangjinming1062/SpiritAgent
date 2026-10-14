"""按当前呈现和业务对象共同决定本回合可披露的动作工具。"""

from features.actions.expression_catalog import has_active_pack
from features.companion.proactive_runtime import get_presentation_snapshot
from features.desktop_life.queries import desktop_state_snapshot
from runtime.tools.presentation import DESKTOP_ACTION_TOOL_NAMES, WINDOW_ACTION_TOOL_NAMES
from sqlalchemy.ext.asyncio import AsyncSession


async def unavailable_action_tool_names(db: AsyncSession, user_id: int) -> frozenset[str]:
    presentation = get_presentation_snapshot(user_id)
    if presentation is not None and presentation.mode == "desktop":
        state = await desktop_state_snapshot(db, user_id)
        return frozenset() if state is not None and state.current_set_id is not None else DESKTOP_ACTION_TOOL_NAMES
    return frozenset() if await has_active_pack(db, user_id) else WINDOW_ACTION_TOOL_NAMES
