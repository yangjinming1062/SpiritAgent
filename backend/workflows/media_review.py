"""媒体复核与动作采纳的跨业务事务。"""

from features.actions.reviewed_assets import ActionReviewStateError, accept_reviewed_action, reject_reviewed_action
from features.media.review_service import (
    MediaReviewSnapshot,
    get_media_review,
    lock_media_review,
    set_media_review_status,
)
from infrastructure.database import SESSION_LOCAL


class MediaReviewStateError(RuntimeError):
    """复核项对应的素材或目录状态已变化。"""


async def accept_media_review(user_id: int, review_id: int) -> MediaReviewSnapshot | None:
    async with SESSION_LOCAL() as db:
        row = await lock_media_review(db, user_id, review_id)
        if row is None or row.status != "pending":
            return row
        await set_media_review_status(db, user_id, review_id, "accepted")
        if row.publication is not None:
            try:
                adopted = await accept_reviewed_action(
                    db,
                    user_id,
                    row.publication.pack_id,
                    row.publication.action_id,
                    row.media_url,
                )
            except ActionReviewStateError as exc:
                raise MediaReviewStateError(str(exc)) from exc
            if not adopted:
                await set_media_review_status(db, user_id, review_id, "rejected")
                await db.commit()
                raise MediaReviewStateError("待确认动作已失效，请刷新后重试")
        await db.commit()
    return await get_media_review(user_id, review_id)


async def reject_media_review(user_id: int, review_id: int) -> MediaReviewSnapshot | None:
    async with SESSION_LOCAL() as db:
        row = await lock_media_review(db, user_id, review_id)
        if row is None or row.status != "pending":
            return row
        await set_media_review_status(db, user_id, review_id, "rejected")
        if row.publication is not None:
            try:
                await reject_reviewed_action(db, user_id, row.publication.action_id, row.media_url)
            except ActionReviewStateError as exc:
                raise MediaReviewStateError(str(exc)) from exc
        await db.commit()
    return await get_media_review(user_id, review_id)
