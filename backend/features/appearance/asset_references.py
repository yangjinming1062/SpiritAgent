from infrastructure.assets.references import asset_paths
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FullbodyCandidate, FullbodyReviewJob


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    released: set[str] = set()
    for row in await db.scalars(
        select(FullbodyCandidate).where(
            FullbodyCandidate.user_id == user_id,
            FullbodyCandidate.status.in_(("accepted", "rejected")),
        ),
    ):
        review = await db.scalar(select(FullbodyReviewJob).where(FullbodyReviewJob.candidate_id == row.id))
        if review is not None:
            review.state, review.stage = (
                ("succeeded", "complete") if row.status == "accepted" else ("cancelled", "cancelled")
            )
        released |= asset_paths([row.image_url, row.base_fullbody_url], user_id)
        await db.delete(row)
    return released
