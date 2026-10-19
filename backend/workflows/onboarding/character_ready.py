"""角色资料完整发布后，按原顺序派生首次动作包和场景。"""

from features.scenes.service import schedule_initial_scene

from .initial_appearance import start_initial_video


async def complete_character_initialization(user_id: int) -> None:
    await start_initial_video(user_id)
    await schedule_initial_scene(user_id)
