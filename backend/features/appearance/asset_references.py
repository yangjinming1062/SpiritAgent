from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .character_card_models import CompanionCharacterCard
from .models import AvatarAsset, CompanionOutfit, FullbodyCandidate


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for model, fields in (
        (AvatarAsset, ("asset_url", "seed_fullbody_url")),
        (CompanionCharacterCard, ("portrait_source_path", "body_source_path")),
        (CompanionOutfit, ("fullbody_url", "source_json")),
    ):
        for row in await db.scalars(select(model).where(model.user_id == user_id)):
            referenced |= asset_paths([getattr(row, field) for field in fields], user_id)
    for row in await db.scalars(
        select(FullbodyCandidate).where(
            FullbodyCandidate.user_id == user_id,
            FullbodyCandidate.status.in_(("pending", "ready", "failed", "analyzing")),
        ),
    ):
        referenced |= asset_paths([row.image_url, row.base_fullbody_url], user_id)
    return referenced


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    released: set[str] = set()
    for row in await db.scalars(
        select(FullbodyCandidate).where(
            FullbodyCandidate.user_id == user_id,
            FullbodyCandidate.status.in_(("accepted", "rejected")),
        ),
    ):
        released |= asset_paths([row.image_url, row.base_fullbody_url], user_id)
        await db.delete(row)
    return released
