"""动作素材复核的事务操作；持久状态和目录发布归动作模块。"""

from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.asset_retirement import action_asset_paths, retire_action_assets
from features.actions.generation.queries import _can_publish_jobs, _job_result_unresolved, _load_generation_context
from features.actions.generation.tasks import _kick_generate
from features.actions.materials import accept_action_asset, accepted_action_asset
from features.actions.models import CompanionAction, CompanionActionPack
from features.actions.publishing import CatalogValidationError, emit_catalog_changed, publish_action_catalog
from features.actions.repository import StaleCatalogError, clear_action_attempt
from features.actions.usage import fulfill_deferred_play_intents
from features.appearance.avatar_service import get_avatar_job_lock
from features.media.chain import MediaChainState
from features.media.character_images import ImageChainState


class ActionReviewStateError(RuntimeError):
    pass


def _is_current_review_asset(action: CompanionAction, media_url: str) -> bool:
    if action.media_path == media_url:
        return True
    if not action.generation_state_json:
        return False
    state = MediaChainState.model_validate_json(action.generation_state_json)
    return any(candidate.path == media_url and candidate.result_json for candidate in state.candidates)


async def accept_reviewed_action(db: AsyncSession, user_id: int, pack_id: int, action_id: int, media_url: str) -> bool:
    """调用方持账户锁；原子采纳单项素材，必需动作齐备时发布，过期候选不替换当前素材。"""
    pack = await db.get(CompanionActionPack, pack_id)
    job = await db.get(CompanionAction, action_id)
    if (
        pack is None
        or pack.user_id != user_id
        or pack.status not in ("ready", "processing", "failed")
        or (job is None)
        or (job.user_id != user_id)
        or (job.pack_id != pack_id)
    ):
        return False
    if job.status in ("queued", "processing"):
        if not _is_current_review_asset(job, media_url):
            return False
        raise ActionReviewStateError("动作素材仍在保存，请稍后重试")
    if job.status not in ("review", "succeeded") or not job.result_json or job.media_path != media_url:
        return False
    previous = accepted_action_asset(job)
    await retire_action_assets(db, user_id, previous.paths() if previous else [])
    job.status = "succeeded"
    accept_action_asset(job)
    await db.flush()
    jobs = (await db.scalars(select(CompanionAction).where(CompanionAction.pack_id == pack.id))).all()
    context = _load_generation_context(pack)
    if _can_publish_jobs(jobs, context.must_actions if context is not None else []):
        try:
            await publish_action_catalog(db, pack)
        except (CatalogValidationError, StaleCatalogError) as exc:
            raise ActionReviewStateError(str(exc) or "动作目录尚未就绪，请稍后重试") from exc
        if pack.active:
            emit_catalog_changed(db, pack)
        if pack.status == "ready":
            await fulfill_deferred_play_intents(db, action_id)
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.action.job_updated",
        payload={
            "packId": pack.id,
            "outfitId": pack.outfit_id,
            "actionId": job.id,
            "action": job.key,
            "stage": job.stage,
        },
    )
    return True


async def reject_reviewed_action(db: AsyncSession, user_id: int, action_id: int, media_url: str) -> None:
    job = await db.get(CompanionAction, action_id)
    if (
        job is not None
        and job.user_id == user_id
        and (job.status in ("queued", "processing"))
        and _is_current_review_asset(job, media_url)
    ):
        raise ActionReviewStateError("动作素材仍在保存，请稍后重试")
    if job is not None and job.user_id == user_id and (job.status == "review") and (job.media_path == media_url):
        unresolved = _job_result_unresolved(job)
        unresolved_state = (job.provider_task_id, job.artifact_path, job.pose_path)
        generation_state = (
            MediaChainState.model_validate_json(job.generation_state_json) if job.generation_state_json else None
        )
        pose_state = (
            ImageChainState.model_validate_json(job.pose_generation_state_json)
            if job.pose_generation_state_json
            else None
        )
        previous_paths = action_asset_paths(job)
        job.status = "failed"
        job.error = "用户未采纳该动作素材"
        clear_action_attempt(job)
        if unresolved:
            for state in (generation_state, pose_state):
                if state is not None:
                    state.candidates = [candidate for candidate in state.candidates if candidate.path != media_url]
            job.generation_state_json = generation_state.model_dump_json() if generation_state is not None else None
            job.pose_generation_state_json = pose_state.model_dump_json() if pose_state is not None else None
            job.provider_task_id, job.artifact_path, job.pose_path = unresolved_state
        await retire_action_assets(db, user_id, previous_paths - action_asset_paths(job))
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.action.job_updated",
            payload={
                "packId": job.pack_id,
                "outfitId": job.outfit_id,
                "actionId": job.id,
                "action": job.key,
                "stage": job.stage,
            },
        )


async def finish_reviewed_pack(user_id: int, pack_id: int) -> None:
    """采纳事务提交后收尾尚未就绪的动作组，沿用身份复核与自动穿着链。"""
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None or pack.user_id != user_id or pack.status not in ("processing", "failed"):
            return
        context = _load_generation_context(pack)
        jobs = (await db.scalars(select(CompanionAction).where(CompanionAction.pack_id == pack_id))).all()
        if context is None or not _can_publish_jobs(jobs, context.must_actions):
            return
        pack.status, pack.error = "processing", None
        await db.commit()
    _kick_generate(pack_id, user_id)
