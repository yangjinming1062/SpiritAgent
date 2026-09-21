"""初始资产恢复；视频启动事实不随任务删除而撤销。"""

from components import SESSION_LOCAL, get_logger
from modules.companion import CompanionOutfit
from modules.ws import emit_ws_event
from sqlalchemy import select

from services.domains.companion import load_character_snapshot

from .avatar_service import get_avatar_job_lock
from .outfit_service import schedule_outfit_description
from .scene_service import schedule_initial_scene
from .video import VideoPackError, create_pack_from_reference

logger = get_logger(__name__)


async def start_initial_video(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        if await load_character_snapshot(db, user_id) is None:
            return
        initial = await db.scalar(
            select(CompanionOutfit).where(
                CompanionOutfit.user_id == user_id,
                CompanionOutfit.status == "ready",
                CompanionOutfit.is_initial.is_(True),
            ),
        )
        if initial is None:
            return
        outfit_id = initial.id
        if not initial.description:
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
                initial = await db.get(CompanionOutfit, outfit_id, populate_existing=True)
                if initial is None or initial.initial_video_started:
                    return
                initial.initial_video_error = message
                emit_ws_event(
                    db,
                    user_id=user_id,
                    event_type="companion.outfit.updated",
                    payload={"outfit_id": outfit_id, "worn": False},
                )
                await db.commit()


async def resume_initial_appearance() -> None:
    async with SESSION_LOCAL() as db:
        users = (
            await db.scalars(
                select(CompanionOutfit.user_id).where(
                    CompanionOutfit.status == "ready",
                    CompanionOutfit.is_initial.is_(True),
                ),
            )
        ).all()
    for user_id in users:
        await start_initial_video(user_id)
        await schedule_initial_scene(user_id)
