"""角色资料完整发布后，请求基础动态准备；初始环境改由引导环境步骤决定后创建。"""

from features.action_library.service import ensure_builtin_actions
from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event


async def complete_character_initialization(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        await ensure_builtin_actions(db, user_id)
        emit_ws_event(db, user_id=user_id, event_type="companion.visual.initial_requested", payload={})
        await db.commit()
