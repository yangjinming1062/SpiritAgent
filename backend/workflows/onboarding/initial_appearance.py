"""初始资产恢复；视频启动事实不随任务删除而撤销。"""

from features.actions.generation.commands import create_pack_from_reference
from features.actions.generation.errors import VideoPackError
from features.appearance.avatar_service import get_avatar_job_lock
from features.appearance.character_card import load_character_snapshot
from features.appearance.onboarding import initial_outfit, initial_outfit_user_ids, record_initial_video_error
from features.appearance.outfit_service import schedule_outfit_description
from features.scenes.service import schedule_initial_scene
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger

from .desktop_suite import prepare_default_desktop_suite

logger = get_logger(__name__)


async def start_initial_video(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        if await load_character_snapshot(db, user_id) is None:
            return
        initial = await initial_outfit(db, user_id)
        if initial is None:
            return
        outfit_id = initial.id
        if not initial.description and initial.description_status == "pending":
            schedule_outfit_description(user_id, outfit_id)
        if initial.initial_video_started:
            return
        await db.commit()
        try:
            await create_pack_from_reference(db, user_id, outfit_id=outfit_id, initial_only=True)
        except Exception as exc:
            await db.rollback()
            logger.warning("initial video scheduling failed", extra={"user_id": user_id}, exc_info=True)
            message = str(exc) if isinstance(exc, VideoPackError) else "默认视频暂时无法生成，请在外观页重试"
            async with get_avatar_job_lock(user_id):
                if not await record_initial_video_error(db, user_id, outfit_id, message):
                    return
                await db.commit()


async def resume_initial_appearance() -> None:
    async with SESSION_LOCAL() as db:
        users = await initial_outfit_user_ids(db)
    for user_id in users:
        await start_initial_video(user_id)
        await schedule_initial_scene(user_id)
        await prepare_default_desktop_suite(user_id, allow_generation=False)
