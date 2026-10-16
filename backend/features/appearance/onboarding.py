"""引导所建立的初始衣柜资料。"""

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
