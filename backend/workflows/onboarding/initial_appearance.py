"""初始场景恢复。"""

from features.appearance.onboarding import initial_outfit_user_ids
from features.scenes.service import schedule_initial_scene
from infrastructure.database import SESSION_LOCAL

from workflows.visual_preparation import schedule_base_motion_preparation


async def resume_initial_appearance() -> None:
    async with SESSION_LOCAL() as db:
        users = await initial_outfit_user_ids(db)
    for user_id in users:
        schedule_base_motion_preparation(user_id)
    for user_id in users:
        await schedule_initial_scene(user_id)
