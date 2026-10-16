"""动作制作：catalog。"""

from collections.abc import Sequence

from infrastructure.assets.asset_store import signed_companion_asset_url
from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.generation.script import VideoActionScriptEntry, parse_action_script_entry
from features.actions.materials import accepted_action_asset
from features.actions.media_contracts import (
    ActionResult,
    PendingActionReview,
    VideoActionImageResponse,
    VideoActionVideoResponse,
    VideoPackResponse,
)
from features.actions.models import REQUIRED_SYSTEM_SLOTS, CompanionAction, CompanionActionPack
from features.actions.publishing import emit_catalog_changed
from features.actions.schemas import PeekGeometry, parse_content_rect
from features.actions.usage import fulfill_deferred_play_intents
from features.appearance.avatar_service import get_avatar_job_lock
from features.appearance.character_card import character_snapshot_is_current
from features.appearance.queries import outfit_snapshot
from features.media.chain import MediaChainState
from features.media.review_service import list_pending_media_reviews

from .execution_state import logger
from .helpers import _reference_hash
from .pipeline import _review_pack_identity
from .queries import (
    _action_order,
    _active_avatar,
    _can_publish_jobs,
    _can_resume_job,
    _has_pending_work,
    _job_result_unresolved,
    _jobs_by_pack,
    _load_generation_context,
)
from .references import _require_new_paid_step
from .transactions import _activate_locked, _mark_pack_failed, _publish_catalog


async def _publish_ready(pack_id: int, results: dict[str, ActionResult]) -> None:
    """发布当前着装动作；身份复核与自动启用守卫保留，提交前核对并发制作结果。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None:
            return
        user_id = pack.user_id
        context = _load_generation_context(pack)
    idle = results.get("idle")
    cover_path = idle.cover_path if idle is not None else None
    if context is None:
        review_status, review_reason = "accepted", ""
    elif cover_path:
        review_status, review_reason = await _review_pack_identity(
            pack_id,
            user_id,
            pack,
            context,
            results,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
    else:
        review_status, review_reason = "review", "动作封面不可用，请预览后手动启用"
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None or pack.status != "processing":
            return
        jobs = (await db.scalars(select(CompanionAction).where(CompanionAction.pack_id == pack_id))).all()
        if any(job.status in ("queued", "processing") for job in jobs):
            return
        current_results = {
            job.key: ActionResult.model_validate_json(material.result_json)
            for job in jobs
            if (material := accepted_action_asset(job)) is not None
        }
        if any(current_results.get(slot) != results.get(slot) for slot in REQUIRED_SYSTEM_SLOTS) or (
            context is not None and not _can_publish_jobs(jobs, context.must_actions)
        ):
            return
        outfit = await outfit_snapshot(db, pack.user_id, pack.outfit_id) if pack.outfit_id is not None else None
        avatar = await _active_avatar(db, pack.user_id)
        reference_is_current = outfit is not None and await _reference_hash(outfit, avatar) == pack.reference_hash
        if not reference_is_current and not pack.reference_path:
            await _mark_pack_failed(db, pack, "外观参考已变更，本次生成结果已过期，请重新生成", stage="publish")
            await db.commit()
            return
        pack.status = "ready"
        pack.error = None
        pack.identity_review = review_status
        pack.identity_review_reason = review_reason
        if cover_path:
            pack.cover_path = cover_path
        # 动作目录 = 当前动作包可用清单：从任务行聚合发布；发布失败时不广播目录也不自动启用，保留当前激活包。
        published = await _publish_catalog(db, pack) is not None
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.video.ready",
            payload={"packId": pack.id, "outfitId": pack.outfit_id},
        )
        if published:
            emit_catalog_changed(db, pack)
        if (
            published
            and context is not None
            and outfit is not None
            and outfit.is_initial
            and outfit.active
            and context.active_outfit_id == outfit.id
            and review_status in ("pass", "accepted")
            and reference_is_current
            and await character_snapshot_is_current(db, pack.user_id, context.identity)
        ):
            await _activate_locked(db, pack)
        await db.commit()


async def _publish_dynamic_catalog(pack_id: int) -> None:
    """动态动作完成后发布动作目录快照（CAS），并广播目录变更。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None:
            return
        if await _publish_catalog(db, pack) is None:
            return
        emit_catalog_changed(db, pack)
        await db.commit()


async def _fulfill_pending_intents(action_ids: list[int]) -> None:
    """补发本轮动作制作期间保存、仍在有效期内的表达意图；只兑现本轮目标，已就绪动作的未执行即时请求不借此补播。"""
    async with SESSION_LOCAL() as db:
        for action_id in action_ids:
            await fulfill_deferred_play_intents(db, action_id)
        await db.commit()


def _pack_response(
    pack: CompanionActionPack,
    jobs: Sequence[CompanionAction],
    *,
    pending_reviews: dict[int, PendingActionReview],
) -> VideoPackResponse:
    """列表与单包共用响应装配；资源 URL 仅在出口签名。"""
    context = _load_generation_context(pack)
    reference_pending = context is not None and (
        context.reference_chain.phase == "submitting" or context.reference_chain.stop_reason == "result_unknown"
    )
    actions = []
    for job in sorted(jobs, key=lambda job: _action_order(job.key)):
        script = parse_action_script_entry(job.script_json) if job.script_json else None
        material = accepted_action_asset(job)
        preview_path = job.media_path or (material.media_path if material else "")
        if not preview_path and job.generation_state_json:
            try:
                candidate = MediaChainState.model_validate_json(job.generation_state_json).best()
                if candidate is not None and candidate.result_json:
                    preview_path = candidate.path
            except ValueError:
                logger.warning("action preview progress invalid", extra={"action_id": job.id})
        preview_type = job.media_type if job.media_path or material is None else material.media_type
        response_model = VideoActionImageResponse if preview_type == "image" else VideoActionVideoResponse
        video_values = (
            {"motion_prompt": script.motion_prompt if isinstance(script, VideoActionScriptEntry) else ""}
            if preview_type == "video"
            else {}
        )
        actions.append(
            response_model(
                action=job.key,
                name=job.name,
                status=job.status,
                stage=job.stage,
                error=job.error,
                video_queue=MediaChainState.model_validate_json(job.generation_state_json).video_queue
                if job.generation_state_json
                else None,
                media_url=signed_companion_asset_url(preview_path) if preview_path else None,
                has_ready_asset=material is not None,
                can_regenerate=not _job_result_unresolved(job),
                current_media_url=signed_companion_asset_url(material.media_path) if material else None,
                pending_review=pending_reviews.get(job.id),
                feedback=context.action_feedback.get(job.key, "") if context else "",
                **video_values,
                peek_geometry=PeekGeometry.from_stored_json(job.peek_geometry_json),
                content_rect=parse_content_rect(job.content_rect_json),
            ),
        )
    return VideoPackResponse(
        id=pack.id,
        outfit_id=pack.outfit_id,
        status=pack.status,
        active=pack.active,
        identity_review=pack.identity_review,
        identity_review_reason=pack.identity_review_reason,
        content_hash=pack.content_hash or None,
        manifest_url=signed_companion_asset_url(pack.manifest_path) if pack.status == "ready" else None,
        error=pack.error or None,
        can_regenerate=(
            bool(pack.reference_path)
            and pack.status in ("processing", "ready", "failed")
            and context is not None
            and context.reference_alignment == "ready"
        ),
        can_regenerate_pack=(
            pack.status in ("ready", "failed")
            and not reference_pending
            and not any(accepted_action_asset(job) is not None for job in jobs)
            and not any(_job_result_unresolved(job) for job in jobs)
            and not _has_pending_work(pack.id, jobs)
        ),
        can_retry=(
            bool(pack.reference_path)
            and pack.status == "failed"
            and context is not None
            and not reference_pending
            and context.reference_alignment != "running"
            and (any(_can_resume_job(job) for job in jobs) or _can_publish_jobs(jobs, context.must_actions))
        ),
        actions=actions,
    )


async def load_pack_response(db: AsyncSession, pack: CompanionActionPack) -> VideoPackResponse:
    jobs = (await db.scalars(select(CompanionAction).where(CompanionAction.pack_id == pack.id))).all()
    return _pack_response(
        pack,
        jobs,
        pending_reviews=await _pending_action_reviews(pack.user_id),
    )


async def list_pack_responses(db: AsyncSession, user_id: int) -> list[VideoPackResponse]:
    """用户的动作包列表（按创建时间倒序）；manifest URL 现签。"""
    packs = (
        (
            await db.execute(
                select(CompanionActionPack)
                .where(CompanionActionPack.user_id == user_id)
                .order_by(CompanionActionPack.created_at.desc()),
            )
        )
        .scalars()
        .all()
    )
    if not packs:
        return []
    jobs_by_pack = await _jobs_by_pack(db, CompanionAction.user_id == user_id)
    pending_reviews = await _pending_action_reviews(user_id)
    return [
        _pack_response(
            pack,
            jobs_by_pack.get(pack.id, []),
            pending_reviews=pending_reviews,
        )
        for pack in packs
    ]


async def _pending_action_reviews(user_id: int) -> dict[int, PendingActionReview]:
    result: dict[int, PendingActionReview] = {}
    for review in await list_pending_media_reviews(user_id):
        if review.publication is None or review.media_type not in ("image", "video"):
            continue
        result.setdefault(
            review.publication.action_id,
            PendingActionReview(
                id=review.id,
                media_type=review.media_type,
                media_url=signed_companion_asset_url(review.media_url),
            ),
        )
    return result
