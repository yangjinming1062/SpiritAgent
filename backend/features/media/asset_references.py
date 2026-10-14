from datetime import timedelta

from infrastructure.assets.references import asset_paths
from infrastructure.text import utc_now
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .queries import live_video_asset_references
from .review_models import CompanionMediaReview


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced = asset_paths([row.model_dump() for row in await live_video_asset_references(db, user_id)], user_id)
    for row in await db.scalars(
        select(CompanionMediaReview).where(
            CompanionMediaReview.user_id == user_id,
            (CompanionMediaReview.status == "pending")
            | (CompanionMediaReview.updated_at >= utc_now() - timedelta(days=7)),
        ),
    ):
        referenced |= asset_paths([row.media_url, row.publication], user_id)
    return referenced


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    released: set[str] = set()
    for row in await db.scalars(
        select(CompanionMediaReview).where(
            CompanionMediaReview.user_id == user_id,
            CompanionMediaReview.status != "pending",
            CompanionMediaReview.updated_at < utc_now() - timedelta(days=7),
        ),
    ):
        released |= asset_paths([row.media_url, row.publication], user_id)
        row.media_url = ""
        row.publication = None
    return released
