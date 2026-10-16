"""确认入柜后受理基础动作；恢复只补接尚未受理且没有明确失败的着装。"""

from features.actions.generation.commands import create_pack_from_reference
from features.actions.generation.errors import VideoPackError
from features.appearance.avatar_service import get_avatar_job_lock
from features.appearance.character_card import load_character_snapshot
from features.appearance.outfit_service import schedule_outfit_description
from features.appearance.queries import outfit_snapshot, pending_outfit_actions, record_initial_video_error
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.user_maintenance import is_user_in_maintenance

logger = get_logger(__name__)


async def start_outfit_actions(user_id: int, outfit_id: int) -> None:
    if is_user_in_maintenance(user_id):
        return
    async with SESSION_LOCAL() as db:
        outfit = await outfit_snapshot(db, user_id, outfit_id)
        if (
            outfit is None
            or outfit.status != "ready"
            or outfit.initial_video_started
            or outfit.initial_video_error is not None
        ):
            return
        if await load_character_snapshot(db, user_id) is None:
            return
        if not outfit.description and outfit.description_status == "pending":
            schedule_outfit_description(user_id, outfit_id)
        await db.commit()
        try:
            await create_pack_from_reference(db, user_id, outfit_id=outfit_id, create_only=True)
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "outfit base action scheduling failed",
                extra={"user_id": user_id, "outfit_id": outfit_id},
                exc_info=True,
            )
            message = str(exc) if isinstance(exc, VideoPackError) else "基础动作暂时无法准备，请在衣柜重试"
            async with get_avatar_job_lock(user_id):
                if await record_initial_video_error(db, user_id, outfit_id, message):
                    await db.commit()


async def resume_outfit_actions(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        pending = await pending_outfit_actions(db, user_id)
    for owner_id, outfit_id in pending:
        await start_outfit_actions(owner_id, outfit_id)
