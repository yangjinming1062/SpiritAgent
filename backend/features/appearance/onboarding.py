"""引导所建立的初始衣柜资料。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionOutfit


async def initial_outfit_user_ids(db: AsyncSession) -> tuple[int, ...]:
    return tuple(
        await db.scalars(
            select(CompanionOutfit.user_id).where(
                CompanionOutfit.status == "ready",
                CompanionOutfit.is_initial.is_(True),
            ),
        ),
    )
