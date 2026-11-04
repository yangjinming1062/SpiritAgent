"""手动及自主造型共用检查点，保存与生活结算保留各自权限。"""

import asyncio

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import client_asset_url
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from infrastructure.video_processing.ffmpeg import VideoProcessError
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.companion.visual_state import persona_snapshot
from features.media.character_images import ImageChainState, review_image_chain, run_frozen_image_chain
from features.media.continuations import MediaWait, authorize_media_waits, finish_media_operation
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.image_generation import ImageGenerationError
from features.media.manual_reviews import (
    ManualAction,
    approve_image_chain,
    manual_candidate_views,
    protect_manual_assets,
    regenerate_image_chain,
)
from features.media.review_contracts import candidate_review_key, needs_review, review_basis, review_passes
from features.media.review_evidence import review_media_intact
from features.media.review_views import (
    MediaJobView,
    MediaManualRequest,
    MediaReviewRequest,
    review_request_matches,
    single_slot_review_view,
)

from .asset_bindings import commit_appearance
from .avatar_service import read_portrait_bytes, validate_transparent_portrait
from .identity import visual_identity_is_current
from .models import AvatarAsset, CompanionOutfit, OutfitCreationJob, OutfitSource
from .outfit_service import get_outfit_policy


async def outfit_review_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(OutfitCreationJob).where(OutfitCreationJob.id == job_id, OutfitCreationJob.user_id == user_id)
        )
        if row is None:
            raise RequestError(status_code=404, message="找不到造型制作任务")
        chain = ImageChainState.model_validate((row.checkpoint or {}).get("image_chain", {}))
        candidate = chain.best() or next(iter(chain.candidates), None)
        report = candidate.review if candidate else None
        evidence = candidate.evidence if candidate else None
        candidate_key = candidate_review_key(chain.generation_id) if candidate else None
        basis = review_basis(report, evidence, candidate_key)
        return single_slot_review_view(
            row,
            media_id=str(row.id),
            review=report,
            basis_id=basis,
            candidate_key=candidate_key,
            preview_url=client_asset_url(candidate.path) if candidate else None,
            can_review=row.stage == "review_pending"
            and (evidence is not None or candidate is not None)
            and needs_review(report),
            candidates=await manual_candidate_views(chain.candidates),
            error=row.error,
            can_regenerate=chain.inputs is not None and row.state in {"paused", "failed"},
        )


async def list_outfit_review_jobs(user_id: int) -> list[MediaJobView]:
    async with SESSION_LOCAL() as db:
        ids = list(
            await db.scalars(
                select(OutfitCreationJob.id)
                .where(OutfitCreationJob.user_id == user_id, OutfitCreationJob.state == "paused")
                .order_by(OutfitCreationJob.id.desc())
                .limit(50)
            )
        )
    return [await outfit_review_view(user_id, job_id) for job_id in ids]


async def review_outfit_job(user_id: int, job_id: int, slot: int, expected: MediaReviewRequest) -> MediaJobView:
    if slot != 0:
        raise RequestError(status_code=400, message="无效的检查槽位")
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(OutfitCreationJob)
            .where(OutfitCreationJob.id == job_id, OutfitCreationJob.user_id == user_id)
            .with_for_update()
        )
        if row is None or row.state in {"cancelled", "succeeded", "failed"} or row.stage != "review_pending":
            raise RequestError(status_code=409, message="检查状态已变化")
        chain = ImageChainState.model_validate((row.checkpoint or {}).get("image_chain", {}))
        if not review_request_matches(await outfit_review_view(user_id, job_id), expected):
            raise RequestError(status_code=409, message="检查依据或任务版本已变化")
        row.state, row.stage = "running", "reviewing"
        await db.commit()

    async def save(value: ImageChainState) -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(OutfitCreationJob, job_id, with_for_update=True)
            if current.state != "running":
                raise ValueError("造型检查已停止")
            current.checkpoint = {**(current.checkpoint or {}), "image_chain": value.model_dump(mode="json")}
            await db.commit()

    try:
        await review_image_chain(user_id, chain, task=AiTaskKey.outfit, save_progress=save)
    except ImageGenerationError:
        pass
    finally:
        async with SESSION_LOCAL() as db:
            current = await db.get(OutfitCreationJob, job_id, with_for_update=True)
            if current.state == "running":
                current.state, current.stage = "paused", "confirmation" if chain.best() else "review_pending"
                emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
                await db.commit()
    return await outfit_review_view(user_id, job_id)


async def continue_outfit_creation(db: AsyncSession, wait: MediaWait) -> dict | None:
    row = await db.scalar(
        select(OutfitCreationJob)
        .where(OutfitCreationJob.id == wait.owner_job_id, OutfitCreationJob.user_id == wait.user_id)
        .with_for_update()
    )
    if row is None or row.state == "cancelled":
        return {"cancelled": True}
    if row.state == "succeeded":
        return {"outfit_id": str(row.result_outfit_id)}
    if row.state in {"queued", "running"}:
        return None
    progress = row.checkpoint or {}
    if progress.get("permission_wait"):
        frozen = progress.get("identity")
        persona = await persona_snapshot(db, wait.user_id)
        if (
            row.request_source == "autonomous"
            and await user_is_active(db, wait.user_id)
            and persona
            and persona.is_complete
            and await get_outfit_policy(db, wait.user_id) != "locked"
            and frozen
            and await visual_identity_is_current(db, wait.user_id, VisualIdentitySnapshot.model_validate(frozen))
        ):
            row.checkpoint = {**progress, "permission_wait": False}
            row.state, row.stage, row.error = "queued", "prepare", None
            emit_ws_event(
                db, user_id=wait.user_id, event_type="outfit.creation.continue", payload={"job_id": str(row.id)}
            )
        return None
    chain = ImageChainState.model_validate(progress.get("image_chain", {}))
    candidate = chain.best()
    if candidate is None or not review_passes(candidate.review, ("identity", "goal", "quality")):
        return None
    if not await asyncio.to_thread(review_media_intact, candidate.review.evidence, candidate.path):
        return None
    try:
        image = await asyncio.to_thread(read_portrait_bytes, candidate.path)
        if image is None:
            raise VideoProcessError("候选图片无法读取，请重新生成")
        await asyncio.to_thread(validate_transparent_portrait, image[0])
    except VideoProcessError as exc:
        row.error = str(exc)
        return None
    if not await user_is_active(db, wait.user_id):
        return None
    if row.request_source == "autonomous":
        persona = await persona_snapshot(db, wait.user_id)
        if persona is None or not persona.is_complete:
            row.error = "角色资料尚未就绪，候选已保留，原业务继续等待"
            return None
        if await get_outfit_policy(db, wait.user_id) == "locked":
            row.error = "自主添置已关闭，候选已保留，原业务继续等待"
            return None
        avatar = await db.scalar(
            select(AvatarAsset).where(AvatarAsset.user_id == wait.user_id, AvatarAsset.active.is_(True))
        )
        frozen = progress.get("identity")
        if (
            frozen is None
            or not await visual_identity_is_current(db, wait.user_id, VisualIdentitySnapshot.model_validate(frozen))
            or (
                avatar is None
                or avatar.identity_revision != progress.get("identity_revision")
                or avatar.seed_fullbody_url != progress.get("identity_path")
            )
        ):
            row.error = "原任务身份已失效，候选已保留，不能按当前身份采纳"
            return None
        row.state, row.stage, row.error = "queued", "prepare", None
        emit_ws_event(db, user_id=wait.user_id, event_type="outfit.creation.continue", payload={"job_id": str(row.id)})
        return None
    frozen = progress["manual_input"]
    identity = VisualIdentitySnapshot.model_validate(frozen["identity"])
    if not await visual_identity_is_current(db, wait.user_id, identity):
        row.error = "原任务身份已失效，候选已保留，不能按当前身份采纳"
        return None
    outfit_id = progress["outfit_id"]
    outfit = await db.scalar(
        select(CompanionOutfit)
        .where(CompanionOutfit.id == outfit_id, CompanionOutfit.user_id == wait.user_id)
        .with_for_update()
    )
    if outfit is not None and outfit.fullbody_url != frozen.get("expected_url"):
        row.error = "原造型版本已变化，候选已保留，不能覆盖当前版本"
        return None
    source = OutfitSource(
        description=frozen.get("description") or row.description,
        identity_revision=identity.revision,
        identity_reference_path=frozen["identity_reference"],
        review_result=candidate.review,
    )
    if outfit is None:
        outfit = CompanionOutfit(
            id=outfit_id,
            user_id=wait.user_id,
            name="新外观",
            fullbody_url=candidate.path,
            source_json=source.dump(),
            status="draft",
        )
        db.add(outfit)
        await db.flush()
    else:
        outfit.fullbody_url, outfit.source_json, outfit.status = candidate.path, source.dump(), "draft"
    row.result_outfit_id, row.state, row.stage = outfit_id, "succeeded", "complete"
    result = {"outfit_id": str(outfit_id)}
    await finish_media_operation(db, wait.user_id, wait.operation_id, result)
    emit_ws_event(
        db, user_id=wait.user_id, event_type="companion.outfit.updated", payload={"outfit_id": str(outfit_id)}
    )
    emit_ws_event(db, user_id=wait.user_id, event_type="media.review.updated", payload={"job_id": str(row.id)})
    await commit_appearance(db, wait.user_id)
    return result


async def manually_review_outfit(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    row = await db.get(OutfitCreationJob, job_id)
    if request.slot != 0:
        raise RequestError(status_code=409, message="无效的候选槽位")
    chain = ImageChainState.model_validate((row.checkpoint or {}).get("image_chain", {}))
    if row.request_source == "manual" and not await visual_identity_is_current(
        db, user_id, VisualIdentitySnapshot.model_validate(row.checkpoint["manual_input"]["identity"])
    ):
        raise RequestError(status_code=409, message="原任务身份已失效，不能按当前身份采纳")
    if row.request_source == "autonomous" and (
        row.checkpoint.get("identity") is None
        or not await visual_identity_is_current(
            db, user_id, VisualIdentitySnapshot.model_validate(row.checkpoint["identity"])
        )
    ):
        raise RequestError(status_code=409, message="原任务身份已失效，不能按当前身份采纳")
    if request.action == "regenerate":
        fresh = regenerate_image_chain(chain)
        row.checkpoint = {key: value for key, value in row.checkpoint.items() if key != "failure_code"}
        row.checkpoint = {**row.checkpoint, "image_chain": fresh.model_dump(mode="json"), "permission_wait": False}
        row.not_before = None
        row.state, row.stage, row.error = "queued", "manual_image", None
        await authorize_media_waits(db, user_id, job_id, "outfit.create")
        return ManualAction(restart_job_id=job_id)
    await approve_image_chain(user_id, chain, request, db=db, required=("identity", "goal", "quality"))
    row.checkpoint = {key: value for key, value in row.checkpoint.items() if key != "failure_code"}
    row.checkpoint = {**row.checkpoint, "image_chain": chain.model_dump(mode="json"), "permission_wait": False}
    row.not_before = None
    row.state, row.stage, row.error = "paused", "confirmation", None
    await authorize_media_waits(db, user_id, job_id, "outfit.create")
    return ManualAction()


async def run_manual_outfit_job(user_id: int, job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(OutfitCreationJob, job_id, with_for_update=True)
        if row is None or row.user_id != user_id or row.state != "queued":
            return
        chain = ImageChainState.model_validate(row.checkpoint["image_chain"])
        row.state, row.stage = "running", "manual_image"
        await db.commit()

    async def save(value: ImageChainState) -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(OutfitCreationJob, job_id, with_for_update=True)
            if current is None or current.state != "running":
                raise ValueError("造型制作已停止")
            current.checkpoint = {**current.checkpoint, "image_chain": value.model_dump(mode="json")}
            await protect_manual_assets(db, user_id, job_id, current.checkpoint)
            await db.commit()

    error = None
    try:
        await run_frozen_image_chain(user_id, chain, task=AiTaskKey.outfit, save_progress=save)
    except ImageGenerationError as exc:
        error = str(exc)
    finally:
        async with SESSION_LOCAL() as db:
            current = await db.get(OutfitCreationJob, job_id, with_for_update=True)
            if current and current.state == "running":
                current.checkpoint = {**current.checkpoint, "image_chain": chain.model_dump(mode="json")}
                current.state, current.stage = (
                    ("unknown", "result_unknown")
                    if chain.phase == "submitting"
                    else ("paused", "confirmation" if chain.best() else "review_pending")
                )
                current.error = error
                emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
                await db.commit()
