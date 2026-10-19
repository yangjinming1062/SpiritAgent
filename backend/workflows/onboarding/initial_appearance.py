"""初始动作受理与场景恢复。"""

from features.appearance.onboarding import initial_outfit, initial_outfit_user_ids
from features.scenes.service import schedule_initial_scene
from infrastructure.database import SESSION_LOCAL

from workflows.outfit_actions import start_outfit_actions


async def start_initial_video(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        initial = await initial_outfit(db, user_id)
    if initial is not None:
        await start_outfit_actions(user_id, initial.id)


async def resume_initial_appearance() -> None:
    async with SESSION_LOCAL() as db:
        users = await initial_outfit_user_ids(db)
    for user_id in users:
        await schedule_initial_scene(user_id)
