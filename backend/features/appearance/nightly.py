"""夜间衣柜读取仅交付不可变业务快照。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionOutfit


@dataclass(frozen=True)
class OutfitSnapshot:
    id: int
    user_id: int
    name: str
    description: str | None
    status: str


async def list_outfits(db: AsyncSession, user_id: int) -> list[OutfitSnapshot]:
    rows = await db.scalars(
        select(CompanionOutfit).where(CompanionOutfit.user_id == user_id).order_by(CompanionOutfit.created_at.desc()),
    )
    return [OutfitSnapshot(row.id, row.user_id, row.name, row.description, row.status) for row in rows]
