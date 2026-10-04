"""出镜媒体的用户复核状态；候选内容仍由原生成链保存。"""

from components import SESSION_LOCAL
from modules.companion import CompanionAction, CompanionActionPack, CompanionMediaReview, MediaReviewPublication
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import (
    CatalogValidationError,
    StaleCatalogError,
    accept_action_asset,
    accepted_action_asset,
    action_asset_paths,
    clear_action_attempt,
    emit_catalog_changed,
    fulfill_deferred_play_intents,
    publish_action_catalog,
    retire_action_assets,
)

from .media_chain import MediaChainState


class MediaReviewStateError(RuntimeError):
    """复核项对应的素材或目录状态已变化。"""


async def create_media_review(
    user_id: int,
    media_type: str,
    media_url: str,
    reason: str,
    *,
    publication: MediaReviewPublication | None = None,
) -> int:
    """登记待确认复核项；同一素材只复用仍待确认的记录，已结束的复核不再代表本次提交。"""
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


def _is_current_review_asset(action: CompanionAction, media_url: str) -> bool:
    if action.media_path == media_url:
        return True
    if not action.generation_state_json:
        return False
    state = MediaChainState.model_validate_json(action.generation_state_json)
    return any(candidate.path == media_url and candidate.result_json for candidate in state.candidates)


async def _accept_reviewed_action(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    action_id: int,
    media_url: str,
) -> bool:
    """采纳已核对的动作素材，并在同一事务发布可播放目录、兑现制作期间保存的表达意图；动作或成品已失效（含成品被替换）时返回 False，成品仍在保存时抛 MediaReviewStateError。"""
    pack = await db.get(CompanionActionPack, pack_id)
    job = await db.get(CompanionAction, action_id)
    if (
        pack is None
        or pack.user_id != user_id
        or pack.status != "ready"
        or job is None
        or job.user_id != user_id
        or job.pack_id != pack_id
    ):
        return False
    if job.status in ("queued", "processing"):
        if not _is_current_review_asset(job, media_url):
            return False
        # 复核登记与任务收尾分两次提交，保存期间不能结束当前成品的复核。
        raise MediaReviewStateError("动作素材仍在保存，请稍后重试")
    if job.status not in ("review", "succeeded") or not job.result_json or job.media_path != media_url:
        return False
    previous = accepted_action_asset(job)
    await retire_action_assets(db, user_id, previous.paths() if previous else [])
    job.status = "succeeded"
    accept_action_asset(job)
    await db.flush()
    try:
        await publish_action_catalog(db, pack)
    except (CatalogValidationError, StaleCatalogError) as exc:
        raise MediaReviewStateError(str(exc) or "动作目录尚未就绪，请稍后重试") from exc
    if pack.active:
        emit_catalog_changed(db, pack)
    # 制作期间保存的意图按播放契约兑现：补发指令晚于目录变更事件，包已不再激活的记为 rejected。
    await fulfill_deferred_play_intents(db, action_id)
    return True


async def has_pending_action_review(db: AsyncSession, action: CompanionAction) -> bool:
    """动作当前成品是否仍有待用户确认的复核项。"""
    if not action.media_path:
        return False
    review_id = await db.scalar(
        select(CompanionMediaReview.id)
        .where(
            CompanionMediaReview.user_id == action.user_id,
            CompanionMediaReview.media_type == action.media_type,
            CompanionMediaReview.media_url == action.media_path,
            CompanionMediaReview.status == "pending",
        )
        .limit(1),
    )
    return review_id is not None


async def reject_pending_action_reviews(db: AsyncSession, action: CompanionAction) -> None:
    """结束动作仍待确认的复核项（调用方提交）；动作被重做替换后，这些复核项不再对应当前成品。"""
    rows = await db.scalars(
        select(CompanionMediaReview)
        .where(
            CompanionMediaReview.user_id == action.user_id,
            CompanionMediaReview.media_type == action.media_type,
            CompanionMediaReview.status == "pending",
        )
        # 先锁复核行再改动作行，与采纳、拒绝的顺序一致，避免并发时死锁。
        .with_for_update(),
    )
    for row in rows:
        publication = MediaReviewPublication.model_validate(row.publication) if row.publication else None
        if publication is not None and publication.action_id == action.id:
            row.status = "rejected"


async def _reject_reviewed_action(db: AsyncSession, user_id: int, action_id: int, media_url: str) -> None:
    job = await db.get(CompanionAction, action_id)
    if (
        job is not None
        and job.user_id == user_id
        and job.status in ("queued", "processing")
        and _is_current_review_asset(job, media_url)
    ):
        raise MediaReviewStateError("动作素材仍在保存，请稍后重试")
    # 复核项只对应生成它的成品；动作已换成别的成品时，拒绝只结束复核项本身。
    if job is not None and job.user_id == user_id and job.status == "review" and job.media_path == media_url:
        await retire_action_assets(db, user_id, action_asset_paths(job))
        job.status = "failed"
        job.error = "用户未采纳该动作素材"
        # 未采纳成品连同生成进度一并作废，之后的重做是独立新尝试；复核链已收尾无待续句柄，文件保留供该记录查看。
        clear_action_attempt(job)
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
        publication = MediaReviewPublication.model_validate(row.publication) if row.publication else None
        if publication is not None:
            adopted = await _accept_reviewed_action(
                db,
                user_id,
                publication.pack_id,
                publication.action_id,
                row.media_url,
            )
            if not adopted:
                # 对应的动作或成品已失效：复核项随之结束，不再留在待确认列表。
                row.status = "rejected"
                await db.commit()
                raise MediaReviewStateError("待确认动作已失效，请刷新后重试")
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
            publication = MediaReviewPublication.model_validate(row.publication) if row.publication else None
            if publication is not None:
                await _reject_reviewed_action(db, user_id, publication.action_id, row.media_url)
            await db.commit()
        db.expunge(row)
        return row
