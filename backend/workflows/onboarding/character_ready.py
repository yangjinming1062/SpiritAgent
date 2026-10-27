"""角色资料完整发布后，派生首次场景并请求基础动态准备。"""

from features.action_library.service import ensure_builtin_actions
from features.scenes.service import schedule_initial_scene
from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event


async def complete_character_initialization(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        await ensure_builtin_actions(db, user_id)
        emit_ws_event(db, user_id=user_id, event_type="companion.visual.initial_requested", payload={})
        await db.commit()
    await schedule_initial_scene(user_id)
