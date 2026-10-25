from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.entries.queries import entry_asset_paths

from .publication_models import PostPublication


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced = await entry_asset_paths(db, user_id)
    for row in await db.scalars(
        select(PostPublication).where(
            PostPublication.user_id == user_id,
            PostPublication.state.in_(("queued", "running", "paused", "unknown")),
        ),
    ):
        referenced |= asset_paths([row.request_json, row.plan_json, row.checkpoint], user_id)
    return referenced
