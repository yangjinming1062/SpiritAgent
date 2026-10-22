"""角色资料完整发布后，派生首次场景。"""

from features.scenes.service import schedule_initial_scene


async def complete_character_initialization(user_id: int) -> None:
    await schedule_initial_scene(user_id)
