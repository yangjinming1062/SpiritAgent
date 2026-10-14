"""初始衣柜资料与制作失败记录；事务由初始化流程管理。"""

from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionOutfit
from .query_contracts import OutfitSnapshot


async def initial_outfit(db: AsyncSession, user_id: int) -> OutfitSnapshot | None:
    row = await db.scalar(
        select(CompanionOutfit).where(
            CompanionOutfit.user_id == user_id,
            CompanionOutfit.status == "ready",
            CompanionOutfit.is_initial.is_(True),
        ),
    )
    return OutfitSnapshot.model_validate(row) if row is not None else None


async def initial_outfit_user_ids(db: AsyncSession) -> tuple[int, ...]:
    return tuple(
        await db.scalars(
            select(CompanionOutfit.user_id).where(
                CompanionOutfit.status == "ready",
                CompanionOutfit.is_initial.is_(True),
            ),
        ),
    )


async def record_initial_video_error(db: AsyncSession, user_id: int, outfit_id: int, message: str) -> bool:
    row = await db.get(CompanionOutfit, outfit_id, populate_existing=True)
    if row is None or row.user_id != user_id or row.initial_video_started:
        return False
    row.initial_video_error = message
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.outfit.updated",
        payload={"outfit_id": outfit_id, "worn": False},
    )
    return True
