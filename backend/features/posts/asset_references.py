from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CompanionPost, PostPublication


async def live_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    referenced: set[str] = set()
    for row in await db.scalars(select(CompanionPost).where(CompanionPost.user_id == user_id)):
        referenced |= asset_paths([row.body, row.media_url, row.audio_url, row.context_json], user_id)
    for row in await db.scalars(
        select(PostPublication).where(
            PostPublication.user_id == user_id,
            PostPublication.status.in_(("queued", "running", "result_unknown")),
        ),
    ):
        referenced |= asset_paths([row.request_json, row.plan_json, row.progress_json], user_id)
    return referenced
