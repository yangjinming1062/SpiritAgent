"""出镜媒体的用户复核状态；候选内容仍由原生成链保存。"""

from pathlib import Path

from components import SESSION_LOCAL, SETTINGS
from modules.companion import CompanionAction, CompanionActionPack, CompanionMediaReview
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import (
    CatalogValidationError,
    StaleCatalogError,
    publish_action_catalog,
)


class MediaReviewStateError(RuntimeError):
    """复核项对应的素材或目录状态已变化。"""


async def create_media_review(
    user_id: int,
    media_type: str,
    media_url: str,
    reason: str,
    *,
    publication: dict | None = None,
) -> int:
    async with SESSION_LOCAL() as db:
        existing = await db.scalar(
            select(CompanionMediaReview).where(
                CompanionMediaReview.user_id == user_id,
                CompanionMediaReview.media_type == media_type,
                CompanionMediaReview.media_url == media_url,
            ),
        )
        if existing is not None and existing.publication == publication:
            return existing.id
        row = CompanionMediaReview(
            user_id=user_id,
            media_type=media_type,
            media_url=media_url,
            status="pending",
            reason=reason[:500],
            publication=publication,
        )
        db.add(row)
        await db.commit()
        return row.id


async def get_media_review(user_id: int, review_id: int) -> CompanionMediaReview | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(CompanionMediaReview).where(
                CompanionMediaReview.id == review_id,
                CompanionMediaReview.user_id == user_id,
            ),
        )
        if row is not None:
            db.expunge(row)
        return row


async def list_pending_media_reviews(user_id: int) -> list[CompanionMediaReview]:
    async with SESSION_LOCAL() as db:
        rows = (
            (
                await db.execute(
                    select(CompanionMediaReview)
                    .where(CompanionMediaReview.user_id == user_id, CompanionMediaReview.status == "pending")
                    .order_by(CompanionMediaReview.id.desc()),
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            db.expunge(row)
        return list(rows)


async def _accept_reviewed_action(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    action_id: int,
) -> None:
    """采纳已核对的动态动作，并在同一事务发布可播放目录。"""
    pack = await db.get(CompanionActionPack, pack_id)
    job = await db.get(CompanionAction, action_id)
    if (
        pack is None
        or pack.user_id != user_id
        or pack.status != "ready"
        or job is None
        or job.user_id != user_id
        or job.pack_id != pack_id
        or job.status not in ("review", "succeeded")
        or not job.result_json
    ):
        raise MediaReviewStateError("待确认动作已失效，请刷新后重试")
    job.status = "succeeded"
    await db.flush()
    if pack.active:
        assets_dir = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id)
        assets_dir.mkdir(parents=True, exist_ok=True)
        try:
            version = await publish_action_catalog(db, pack, assets_dir=assets_dir)
        except (CatalogValidationError, StaleCatalogError) as exc:
            raise MediaReviewStateError(str(exc) or "动作目录尚未就绪，请稍后重试") from exc
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.action.catalog_changed",
            payload={"packId": pack.id, "catalogVersion": version},
        )


async def _reject_reviewed_action(db: AsyncSession, user_id: int, action_id: int) -> None:
    job = await db.get(CompanionAction, action_id)
    if job is not None and job.user_id == user_id and job.status == "review":
        job.status = "failed"
        job.error = "用户未采纳该动作视频"
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.action.job_updated",
            payload={"packId": job.pack_id, "actionId": job.id, "stage": job.stage},
        )


async def accept_media_review(user_id: int, review_id: int) -> CompanionMediaReview | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(CompanionMediaReview)
            .where(CompanionMediaReview.id == review_id, CompanionMediaReview.user_id == user_id)
            .with_for_update(),
        )
        if row is None:
            return None
        if row.status != "pending":
            db.expunge(row)
            return row
        row.status = "accepted"
        if row.publication and row.publication.get("kind") == "action":
            await _accept_reviewed_action(
                db,
                user_id,
                int(row.publication["pack_id"]),
                int(row.publication["action_id"]),
            )
        await db.commit()
        db.expunge(row)
        return row


async def reject_media_review(user_id: int, review_id: int) -> CompanionMediaReview | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(CompanionMediaReview)
            .where(CompanionMediaReview.id == review_id, CompanionMediaReview.user_id == user_id)
            .with_for_update(),
        )
        if row is None:
            return None
        if row.status == "pending":
            row.status = "rejected"
            if row.publication and row.publication.get("kind") == "action":
                await _reject_reviewed_action(db, user_id, int(row.publication["action_id"]))
            await db.commit()
        db.expunge(row)
        return row
