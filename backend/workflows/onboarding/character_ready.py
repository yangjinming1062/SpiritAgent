"""角色资料完整发布后，派生首次场景。"""

from features.action_library.service import ensure_initial_action
from features.scenes.service import schedule_initial_scene
from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event

from workflows.visuals_companion import load_action_review_source


async def complete_character_initialization(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        source = await load_action_review_source(db, user_id)
        await ensure_initial_action(db, user_id, review_source_json=source.model_dump_json())
        emit_ws_event(db, user_id=user_id, event_type="companion.visual.initial_requested", payload={})
        await db.commit()
    await schedule_initial_scene(user_id)
