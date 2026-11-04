from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.references import scanned_asset_paths
from features.entries.queries import entry_asset_paths

from .publication_models import PostPublication


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced = await entry_asset_paths(db, user_id)
    referenced |= await scanned_asset_paths(
        db,
        user_id,
        select(PostPublication.request_json, PostPublication.plan_json, PostPublication.checkpoint).where(
            PostPublication.user_id == user_id, PostPublication.state.in_(("queued", "running", "paused", "unknown"))
        ),
    )
    referenced |= await scanned_asset_paths(
        db,
        user_id,
        select(PostPublication.review_result_json).where(
            PostPublication.user_id == user_id, PostPublication.post_id.is_not(None)
        ),
    )
    return referenced
