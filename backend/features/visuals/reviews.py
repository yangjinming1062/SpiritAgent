"""影像原任务的查询与仅检查操作，不发布或替换成品。"""

import asyncio
from uuid import uuid4

from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from PIL import Image, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession

from features.appearance.identity import visual_identity_is_current
from features.media.chain import MediaChainState
from features.media.character_images import ImageChainState
from features.media.manual_reviews import (
    ManualAction,
    approve_candidate,
    manual_candidate_id,
    manual_candidate_views,
    regenerate_image_chain,
)
from features.media.review_contracts import (
    candidate_review_key,
    optional_quality_choice,
    review_basis,
    visual_reference_basis,
)
from features.media.review_views import (
    MediaJobView,
    MediaManualRequest,
    single_slot_review_view,
)

from .creation import opening_image_hash, opening_reference_hash, opening_requirements_hash
from .generation import (
    VisualCandidateResult,
    current_visual_candidate,
    restart_video_generation,
    schedule_visual_job,
    visual_quality_candidates,
)
from .life import RECOVERY_WAITING, RESUME_WAITING, settle_visual_steps
from .repository import notify_visuals_updated, visual_job_row, visual_progress, visual_snapshot
from .schemas import OpeningReview, VisualAsset


async def visual_review_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        row = await visual_job_row(db, user_id, job_id)
        progress = visual_progress(row)
        if progress is None:
            raise RequestError(status_code=409, message="检查进度不可用")
        if progress.source != "user_requested" or row.stage != "review_pending":
            return MediaJobView(
                id=row.id,
                kind=row.kind,
                state=row.state,
                stage=row.stage,
                revision=row.revision,
                required_slots=1,
                ready_slots=0,
                complete=row.state == "succeeded",
                slots=[],
            )
        media_type, candidates = visual_quality_candidates(progress)
        options = await manual_candidate_views(candidates, media_type=media_type)
        first = next(iter(options), None)
        review = first.review if first else None
        candidate_key = candidate_review_key(progress.generation_id)
        basis = review_basis(review, None, candidate_key if first else None)
        return single_slot_review_view(
            row,
            media_id=str(row.id),
            review=review,
            basis_id=basis,
            candidate_key=candidate_key,
            preview_url=first.preview_url if first else None,
            can_review=bool(first),
            candidates=options,
            error=row.error,
            can_regenerate=bool(options),
        )


async def manually_review_visual(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    row = await visual_job_row(db, user_id, job_id)
    progress = visual_progress(row)
    source = visual_snapshot(row).source
    if progress is None or progress.source != "user_requested" or row.stage != "review_pending" or request.slot != 0:
        raise RequestError(status_code=409, message="原制作进度已失效")
    if source.kind in {"actor", "cinematic"} and not await visual_identity_is_current(db, user_id, source.identity):
        raise RequestError(status_code=409, message="原任务身份已失效，不能按当前身份采纳")
    media_type, candidates = visual_quality_candidates(progress)
    if request.action == "regenerate":
        if not any(not entry.excluded and optional_quality_choice(entry.review) for entry in candidates):
            raise RequestError(status_code=409, message="这次影像制作没有可选候选")
        if media_type == "video":
            await restart_video_generation(
                user_id, "loop" if source.kind == "scene" else source.action.clip_kind, progress
            )
        else:
            if progress.image_chain_json:
                fresh = regenerate_image_chain(ImageChainState.model_validate_json(progress.image_chain_json))
                progress.image_chain_json, progress.generation_id = fresh.model_dump_json(), fresh.generation_id
            else:
                progress.generation_id = uuid4().hex
            if progress.video_chain_json:
                chain = MediaChainState.model_validate_json(progress.video_chain_json)
                progress.video_chain_json = MediaChainState(
                    providers=chain.configured_providers(), generation_id=progress.generation_id
                ).model_dump_json()
            progress.pose_path, progress.opening_review, progress.candidate = "", None, None
            progress.image_confirmed = False
            progress.review_result, progress.identity_evidence, progress.video_evidence = None, None, None
            row.repair_count = 0
        progress.review_only = False
    else:
        selected = next(
            (entry for entry in candidates if manual_candidate_id(entry) == request.candidate_id),
            None,
        )
        if selected is None:
            raise RequestError(status_code=409, message="所选候选已变化，请刷新")
        await approve_candidate(
            user_id,
            selected,
            ("goal", "quality") if source.kind == "scene" else ("identity", "goal", "quality"),
            request,
            db=db,
        )
        selected.review.reference_basis = visual_reference_basis(source.model_dump(mode="json"))
        chain_field = "video_chain_json" if media_type == "video" else "image_chain_json"
        saved_chain = getattr(progress, chain_field)
        if saved_chain:
            chain_type = MediaChainState if chain_field == "video_chain_json" else ImageChainState
            chain = chain_type.model_validate_json(saved_chain)
            if chain_field == "video_chain_json":
                if progress.candidate is None or progress.candidate.media_path != selected.path:
                    if progress.candidate is not None:
                        chain.candidates.append(current_visual_candidate(progress, chain))
                    progress.source_path = None
                chain.candidates = [entry for entry in chain.candidates if entry.path != selected.path]
                chain.active_index = selected.attempt
                chain.phase, chain.stop_reason = "complete", "accepted"
                chain.queue_request_id = chain.result_url = None
            else:
                chain.candidates = [entry for entry in chain.candidates if entry.path != selected.path] + [selected]
            setattr(progress, chain_field, chain.model_dump_json())
        if media_type == "video" or progress.candidate is not None:
            if media_type == "video" and not selected.result_json:
                raise RequestError(status_code=409, message="所选视频缺少已处理的成品，请重新生成")
            if selected.result_json:
                progress.candidate = VisualCandidateResult.model_validate_json(selected.result_json).asset
            progress.review_result = selected.review
            progress.identity_evidence = progress.video_evidence = None
            progress.review_only = True
        else:
            resolved = resolve_asset_reference(selected.path)
            with Image.open(resolved[0]) as image:
                progress.frame_width, progress.frame_height = ImageOps.exif_transpose(image).size
            progress.pose_path = selected.path
            progress.image_confirmed = True
            if source.kind != "scene":
                progress.opening_review = OpeningReview(
                    image_hash=await asyncio.to_thread(opening_image_hash, selected.path),
                    requirements_hash=opening_requirements_hash(row, progress),
                    reference_hash=opening_reference_hash(source),
                    evidence=selected.review.evidence,
                    human_approval=selected.review.human_approval,
                )
            progress.review_result = selected.review
            if row.target_kind == "image":
                progress.candidate = VisualAsset(
                    media_kind="image",
                    media_path=selected.path,
                    width=progress.frame_width,
                    height=progress.frame_height,
                    visual_description=progress.visual_description,
                    outfit_description=progress.outfit_description or "",
                )
                progress.review_only = True
            else:
                progress.review_only = False
    progress.paused_by_user, progress.failure_reason, progress.submission_unknown = False, "", False
    row.checkpoint = progress.model_dump(mode="json")
    row.state, row.stage = "queued", RECOVERY_WAITING if progress.review_only else RESUME_WAITING
    row.error, row.finished_at = None, None
    await settle_visual_steps(db, user_id, row)
    await notify_visuals_updated(db, user_id)
    return ManualAction(restart_job_id=job_id, audit_context={"snapshot_json": row.snapshot_json})


async def run_manual_visual_job(user_id: int, job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await visual_job_row(db, user_id, job_id)
        progress = visual_progress(row)
        if row.state == "queued" and progress is not None:
            schedule_visual_job(user_id, job_id, allow_model_steps=not progress.review_only)
