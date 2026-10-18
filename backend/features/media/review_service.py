"""媒体复核的持久状态与公开快照；动作采纳由跨业务流程协调。"""

from collections.abc import Collection
from datetime import datetime
from typing import Literal

from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.media.review_contracts import MediaReviewPublication

from .review_models import CompanionMediaReview


class MediaReviewSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True, extra="forbid")

    id: int
    user_id: int
    media_type: str
    media_url: str
    status: str
    reason: str
    publication: MediaReviewPublication | None
    created_at: datetime
    updated_at: datetime


async def create_media_review(
    user_id: int,
    media_type: str,
    media_url: str,
    reason: str,
    *,
    publication: MediaReviewPublication | None = None,
) -> int:
    stored = publication.model_dump() if publication is not None else None
    async with SESSION_LOCAL() as db:
        existing = await db.scalar(
            select(CompanionMediaReview)
            .where(
                CompanionMediaReview.user_id == user_id,
                CompanionMediaReview.media_type == media_type,
                CompanionMediaReview.media_url == media_url,
                CompanionMediaReview.status == "pending",
            )
            .order_by(CompanionMediaReview.id.desc())
            .limit(1),
        )
        if existing is not None and existing.publication == stored:
            return existing.id
        row = CompanionMediaReview(
            user_id=user_id,
            media_type=media_type,
            media_url=media_url,
            status="pending",
            reason=reason[:500],
            publication=stored,
        )
        db.add(row)
        await db.commit()
        return row.id


async def get_media_review(user_id: int, review_id: int) -> MediaReviewSnapshot | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(CompanionMediaReview).where(
                CompanionMediaReview.id == review_id,
                CompanionMediaReview.user_id == user_id,
            ),
        )
        return MediaReviewSnapshot.model_validate(row) if row is not None else None


async def list_pending_media_reviews(user_id: int) -> list[MediaReviewSnapshot]:
    async with SESSION_LOCAL() as db:
        rows = await db.scalars(
            select(CompanionMediaReview)
            .where(CompanionMediaReview.user_id == user_id, CompanionMediaReview.status == "pending")
            .order_by(CompanionMediaReview.id.desc()),
        )
        return [MediaReviewSnapshot.model_validate(row) for row in rows]


async def list_rejected_action_media(
    db: AsyncSession,
    user_id: int,
    action_ids: Collection[int],
) -> dict[int, frozenset[str]]:
    """恢复制作时按账户和动作核对已拒绝素材，不让残留候选重新进入复核。"""
    if not action_ids:
        return {}
    action_id = CompanionMediaReview.publication["action_id"].as_integer()
    rows = await db.execute(
        select(action_id, CompanionMediaReview.media_url).where(
            CompanionMediaReview.user_id == user_id,
            CompanionMediaReview.status == "rejected",
            CompanionMediaReview.publication["kind"].as_string() == "action",
            action_id.in_(action_ids),
            CompanionMediaReview.media_url != "",
        ),
    )
    paths: dict[int, set[str]] = {}
    for review_action_id, media_url in rows:
        paths.setdefault(review_action_id, set()).add(media_url)
    return {key: frozenset(values) for key, values in paths.items()}


async def lock_media_review(db: AsyncSession, user_id: int, review_id: int) -> MediaReviewSnapshot | None:
    row = await db.scalar(
        select(CompanionMediaReview)
        .where(CompanionMediaReview.id == review_id, CompanionMediaReview.user_id == user_id)
        .with_for_update(),
    )
    return MediaReviewSnapshot.model_validate(row) if row is not None else None


async def set_media_review_status(
    db: AsyncSession,
    user_id: int,
    review_id: int,
    status: Literal["accepted", "rejected"],
) -> None:
    row = await db.scalar(
        select(CompanionMediaReview)
        .where(CompanionMediaReview.id == review_id, CompanionMediaReview.user_id == user_id)
        .with_for_update(),
    )
    if row is None:
        raise ValueError("Media review is no longer available")
    row.status = status


async def release_action_reviews_for_packs(db: AsyncSession, user_id: int, pack_ids: Collection[int]) -> set[str]:
    """删除动作组时释放复核素材，保留结论。"""
    if not pack_ids:
        return set()
    rows = await db.scalars(
        select(CompanionMediaReview)
        .where(
            CompanionMediaReview.user_id == user_id,
            CompanionMediaReview.publication["pack_id"].as_integer().in_(pack_ids),
        )
        .with_for_update(),
    )
    released: set[str] = set()
    for row in rows:
        released |= asset_paths([row.media_url, row.publication], user_id)
        row.media_url = ""
        row.publication = None
        if row.status == "pending":
            row.status = "rejected"
    return released


async def has_pending_action_review(db: AsyncSession, *, user_id: int, media_type: str, media_url: str | None) -> bool:
    if not media_url:
        return False
    review_id = await db.scalar(
        select(CompanionMediaReview.id)
        .where(
            CompanionMediaReview.user_id == user_id,
            CompanionMediaReview.media_type == media_type,
            CompanionMediaReview.media_url == media_url,
            CompanionMediaReview.status == "pending",
        )
        .limit(1),
    )
    return review_id is not None


async def reject_pending_action_reviews(db: AsyncSession, *, user_id: int, action_id: int, media_type: str) -> None:
    """调用方先锁复核行再改动作行；采纳、拒绝及删除共享这一顺序。"""
    rows = await db.scalars(
        select(CompanionMediaReview)
        .where(
            CompanionMediaReview.user_id == user_id,
            CompanionMediaReview.media_type == media_type,
            CompanionMediaReview.status == "pending",
        )
        .with_for_update(),
    )
    for row in rows:
        publication = MediaReviewPublication.model_validate(row.publication) if row.publication else None
        if publication is not None and publication.action_id == action_id:
            row.status = "rejected"
