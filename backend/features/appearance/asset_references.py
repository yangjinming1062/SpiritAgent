"""全身候选的保留期与检查依据回收。"""

from datetime import timedelta

from infrastructure.assets.references import asset_paths
from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FullbodyCandidate, FullbodyReviewJob


async def cleanup_obsolete_assets(db: AsyncSession, user_id: int) -> set[str]:
    rows = (
        await db.execute(
            select(FullbodyCandidate, FullbodyReviewJob)
            .outerjoin(FullbodyReviewJob, FullbodyReviewJob.candidate_id == FullbodyCandidate.id)
            .where(FullbodyCandidate.user_id == user_id, FullbodyCandidate.status.in_(("accepted", "rejected")))
        )
    ).all()
    cutoff = utc_now() - timedelta(days=90)
    released: set[str] = set()
    for candidate, review in rows:
        if review is not None:
            review.state, review.stage = (
                ("succeeded", "complete") if candidate.status == "accepted" else ("cancelled", "cancelled")
            )
            await db.flush()
        finished = review.finished_at if review is not None else candidate.updated_at
        if finished is None or ensure_utc(finished) >= cutoff:
            continue
        released |= asset_paths([candidate.image_url, candidate.base_fullbody_url, candidate.review_json], user_id)
        await db.delete(candidate)
    return released
