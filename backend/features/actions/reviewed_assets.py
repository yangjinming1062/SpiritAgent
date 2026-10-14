"""动作素材复核的事务操作；持久状态和目录发布归动作模块。"""

from runtime.events.emit import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.asset_retirement import action_asset_paths, retire_action_assets
from features.actions.materials import accept_action_asset, accepted_action_asset
from features.actions.models import CompanionAction, CompanionActionPack
from features.actions.publishing import CatalogValidationError, emit_catalog_changed, publish_action_catalog
from features.actions.repository import StaleCatalogError, clear_action_attempt
from features.actions.usage import fulfill_deferred_play_intents
from features.media.chain import MediaChainState


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
    """采纳已核对的动作素材，并在同一事务发布可播放目录、兑现制作期间保存的表达意图；动作或成品已失效（含成品被替换）时返回 False，成品仍在保存时抛 MediaReviewStateError。"""
    pack = await db.get(CompanionActionPack, pack_id)
    job = await db.get(CompanionAction, action_id)
    if (
        pack is None
        or pack.user_id != user_id
        or pack.status != "ready"
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
    try:
        await publish_action_catalog(db, pack)
    except (CatalogValidationError, StaleCatalogError) as exc:
        raise ActionReviewStateError(str(exc) or "动作目录尚未就绪，请稍后重试") from exc
    if pack.active:
        emit_catalog_changed(db, pack)
    await fulfill_deferred_play_intents(db, action_id)
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
        await retire_action_assets(db, user_id, action_asset_paths(job))
        job.status = "failed"
        job.error = "用户未采纳该动作素材"
        clear_action_attempt(job)
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.action.job_updated",
            payload={"packId": job.pack_id, "actionId": job.id, "stage": job.stage},
        )
