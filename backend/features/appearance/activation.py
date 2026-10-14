"""动作包激活时，外观所有者在调用方事务内更新穿着状态。"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionOutfit


async def activate_outfit_for_pack(db: AsyncSession, user_id: int, outfit_id: int | None) -> None:
    await db.execute(
        update(CompanionOutfit)
        .where(CompanionOutfit.user_id == user_id)
        .values(active=False)
        .execution_options(synchronize_session=False),
    )
    await db.execute(
        update(CompanionOutfit)
        .where(CompanionOutfit.user_id == user_id, CompanionOutfit.id == outfit_id)
        .values(active=True)
        .execution_options(synchronize_session=False),
    )
