from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionScene


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for row in await db.scalars(select(CompanionScene).where(CompanionScene.user_id == user_id)):
        referenced |= asset_paths(
            [
                row.media_path,
                row.upload_source_path,
                row.generation_state_json,
                row.regeneration_state_json,
                row.reference_image,
            ],
            user_id,
        )
    return referenced
