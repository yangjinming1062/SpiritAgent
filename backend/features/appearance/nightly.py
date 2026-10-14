"""夜间外观读取与操作仅交付不可变业务快照。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionOutfit
from .outfit_service import activate_outfit, confirm_outfit, create_outfit_draft


@dataclass(frozen=True)
class OutfitSnapshot:
    id: int
    user_id: int
    name: str
    description: str | None
    status: str
    active: bool


def _snapshot(row: CompanionOutfit) -> OutfitSnapshot:
    return OutfitSnapshot(row.id, row.user_id, row.name, row.description, row.status, row.active)


async def list_outfits(db: AsyncSession, user_id: int) -> list[OutfitSnapshot]:
    rows = await db.scalars(
        select(CompanionOutfit).where(CompanionOutfit.user_id == user_id).order_by(CompanionOutfit.created_at.desc()),
    )
    return [_snapshot(row) for row in rows]


async def get_outfit(db: AsyncSession, user_id: int, outfit_id: int) -> OutfitSnapshot | None:
    row = await db.get(CompanionOutfit, outfit_id)
    return _snapshot(row) if row is not None and row.user_id == user_id else None


async def wear_outfit(
    db: AsyncSession,
    user_id: int,
    outfit_id: int,
    *,
    require_current_identity: bool = False,
) -> OutfitSnapshot:
    return _snapshot(await activate_outfit(db, user_id, outfit_id, require_current_identity=require_current_identity))


async def create_and_confirm_outfit(db: AsyncSession, user_id: int, description: str) -> OutfitSnapshot:
    draft = await create_outfit_draft(db, user_id, description=description)
    return _snapshot(await confirm_outfit(db, user_id, draft.id))
