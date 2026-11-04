"""影像原任务的查询与仅检查操作，不发布或替换成品。"""

import asyncio
from uuid import uuid4

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import client_asset_url, resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from PIL import Image, ImageOps
from runtime.events.emit import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

from features.appearance.identity import visual_identity_is_current
from features.media.chain import MediaCandidate, MediaChainState
from features.media.character_images import ImageChainState, review_image_chain
from features.media.manual_reviews import (
    ManualAction,
    approve_candidate,
    manual_candidate_id,
    manual_candidate_views,
    regenerate_image_chain,
)
from features.media.review_contracts import candidate_review_key, needs_review, review_basis, visual_reference_basis
from features.media.review_views import (
    MediaJobView,
    MediaManualRequest,
    MediaReviewRequest,
    MediaSlotView,
    review_request_matches,
)

from .creation import (
    opening_image_hash,
    opening_reference_hash,
    opening_requirements_hash,
    opening_visual_review,
    review_existing_opening,
)
from .generation import (
    VisualCandidateResult,
    _evaluate_candidate,
    _evaluate_scene_candidate,
    _require_active_job,
    _save_progress,
    restart_video_generation,
    schedule_visual_job,
)
from .repository import notify_visuals_updated, visual_job_row, visual_progress, visual_snapshot
from .schemas import OpeningReview, VisualAsset, VisualJobProgress

_LOCKS: dict[int, asyncio.Lock] = {}


def _manual_candidates(progress: VisualJobProgress) -> list[MediaCandidate]:
    if progress.candidate is not None:
        chain = MediaChainState.model_validate_json(progress.video_chain_json or "{}")
        entries = list(chain.candidates)
        if not any(entry.path == progress.candidate.media_path for entry in entries):
            entries.append(
                MediaCandidate(
                    path=progress.candidate.media_path,
                    attempt=chain.active_index or 0,
                    review=progress.review_result,
                    result_json=VisualCandidateResult(
                        asset=progress.candidate,
                        verdict=progress.review_verdict or "review",
                        reason=progress.review_reason,
                        identity_reason=progress.identity_reason,
                    ).model_dump_json(),
                )
            )
        return entries
    entries = ImageChainState.model_validate_json(progress.image_chain_json or "{}").candidates
    if progress.pose_path and not any(entry.path == progress.pose_path for entry in entries):
        entries = [
            MediaCandidate(
                path=progress.pose_path,
                attempt=0,
                review=opening_visual_review(progress.opening_review) if progress.opening_review else None,
            ),
            *entries,
        ]
    return entries


async def visual_review_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        row = await visual_job_row(db, user_id, job_id)
        progress = visual_progress(row)
        if progress is None:
            raise RequestError(status_code=409, message="检查进度不可用")
        review = progress.review_result
        candidate_path = progress.candidate.media_path if progress.candidate else progress.pose_path
        evidence = progress.video_evidence or progress.identity_evidence
        if progress.image_chain_json and progress.candidate is None:
            chain = ImageChainState.model_validate_json(progress.image_chain_json)
            candidate = chain.best() or next(iter(chain.candidates), None)
            if candidate:
                review, evidence, candidate_path = candidate.review, candidate.evidence, candidate.path
        candidate_key = candidate_review_key(progress.generation_id)
        basis = review_basis(review, evidence, candidate_key if candidate_path else None)
        return MediaJobView(
            id=row.id,
            kind=row.kind,
            state=row.state,
            stage=row.stage,
            revision=row.revision,
            required_slots=1,
            ready_slots=int(review is not None and review.verdict == "pass"),
            complete=row.state == "succeeded",
            slots=[
                MediaSlotView(
                    slot=0,
                    media_id=str(row.id),
                    status=row.stage,
                    review=review,
                    basis_id=basis,
                    candidate_basis_id=candidate_key,
                    preview_url=client_asset_url(candidate_path) if candidate_path else None,
                    can_review=row.stage == "review_pending" and basis is not None and needs_review(review),
                    error=row.error,
                    candidates=await manual_candidate_views(
                        _manual_candidates(progress),
                        media_type="video"
                        if progress.candidate and progress.candidate.media_kind == "video"
                        else "image",
                    ),
                    can_regenerate=row.state in {"paused", "failed"},
                )
            ],
        )


async def review_visual_job(user_id: int, job_id: int, slot: int, expected: MediaReviewRequest) -> MediaJobView:
    if slot != 0:
        raise RequestError(status_code=400, message="无效的检查槽位")
    async with _LOCKS.setdefault(job_id, asyncio.Lock()):
        async with SESSION_LOCAL() as db:
            row = await visual_job_row(db, user_id, job_id, lock=True)
            progress = visual_progress(row)
            if row.stage != "review_pending" or progress is None:
                raise RequestError(status_code=409, message="检查状态已变化")
            if not review_request_matches(await visual_review_view(user_id, job_id), expected):
                raise RequestError(status_code=409, message="检查依据或任务版本已变化")
            row.state, row.stage = "running", "reviewing"
            progress.review_only = True
            row.checkpoint = progress.model_dump(mode="json")
            await db.commit()
        error = None
        try:
            if progress.candidate and progress.candidate.media_kind == "video":
                if visual_snapshot(row).kind == "scene":
                    await _evaluate_scene_candidate(user_id, row, progress)
                else:
                    await _evaluate_candidate(user_id, row, progress)
            elif progress.pose_path and visual_snapshot(row).source.kind in {"actor", "cinematic"}:

                async def before_submit() -> None:
                    await _require_active_job(user_id, job_id, progress.generation_id)

                async def save_opening_review() -> None:
                    await _save_progress(user_id, job_id, progress, stage="checking")

                opening = await review_existing_opening(
                    user_id,
                    row,
                    progress,
                    snapshot=visual_snapshot(row).source,
                    before_submit=before_submit,
                    save_progress=save_opening_review,
                )
                progress.review_result = opening_visual_review(opening)
                progress.review_result.reference_basis = visual_reference_basis(
                    visual_snapshot(row).source.model_dump(mode="json")
                )
            elif progress.image_chain_json:
                chain = ImageChainState.model_validate_json(progress.image_chain_json)

                async def save(value: ImageChainState) -> None:
                    progress.image_chain_json = value.model_dump_json()
                    async with SESSION_LOCAL() as db:
                        current = await visual_job_row(db, user_id, job_id, lock=True)
                        if current.state != "running" or current.stage not in {"reviewing", "review"}:
                            raise asyncio.CancelledError()
                        current.checkpoint = progress.model_dump(mode="json")
                        await db.commit()

                await review_image_chain(user_id, chain, task=AiTaskKey.visuals, save_progress=save)
                chosen = chain.best()
                progress.review_result = (
                    chosen.review.model_copy(
                        update={
                            "reference_basis": visual_reference_basis(
                                visual_snapshot(row).source.model_dump(mode="json")
                            )
                        }
                    )
                    if chosen and chosen.review
                    else None
                )
            else:
                raise ValueError("原检查证据不可用")
        except Exception as exc:
            error = str(exc)
        finally:
            async with SESSION_LOCAL() as db:
                current = await visual_job_row(db, user_id, job_id, lock=True)
                saved = visual_progress(current)
                if current.state == "running" and saved is not None and saved.generation_id == progress.generation_id:
                    current.state = "paused"
                    current.stage = (
                        "confirmation"
                        if progress.review_result and progress.review_result.verdict != "review"
                        else "review_pending"
                    )
                    current.error = error
                    current.checkpoint = progress.model_dump(mode="json")
                    await notify_visuals_updated(db, user_id)
                    emit_ws_event(
                        db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)}
                    )
                    await db.commit()
    return await visual_review_view(user_id, job_id)


async def manually_review_visual(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    row = await visual_job_row(db, user_id, job_id)
    progress = visual_progress(row)
    source = visual_snapshot(row).source
    if progress is None or request.slot != 0:
        raise RequestError(status_code=409, message="原制作进度已失效")
    if source.kind in {"actor", "cinematic"} and not await visual_identity_is_current(db, user_id, source.identity):
        raise RequestError(status_code=409, message="原任务身份已失效，不能按当前身份采纳")
    if request.action == "regenerate":
        if progress.candidate and progress.candidate.media_kind == "video":
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
                    providers=chain.providers, generation_id=progress.generation_id
                ).model_dump_json()
            progress.pose_path, progress.opening_review, progress.candidate = "", None, None
            progress.image_confirmed = False
            progress.review_result, progress.identity_evidence, progress.video_evidence = None, None, None
            progress.identity_verdict = progress.review_verdict = progress.quality_verdict = None
            row.repair_count = 0
        progress.review_only = False
    else:
        selected = next(
            (entry for entry in _manual_candidates(progress) if manual_candidate_id(entry) == request.candidate_id),
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
        if progress.candidate is not None:
            if selected.result_json:
                progress.candidate = VisualCandidateResult.model_validate_json(selected.result_json).asset
            progress.review_result = selected.review
            progress.review_verdict = progress.review_verdict or "pass"
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
                progress.review_verdict, progress.review_only = "pass", True
            else:
                progress.review_only = False
    progress.paused_by_user, progress.failure_reason, progress.submission_unknown = False, "", False
    row.checkpoint, row.state, row.stage, row.error = progress.model_dump(mode="json"), "queued", "prepare", None
    await notify_visuals_updated(db, user_id)
    return ManualAction(restart_job_id=job_id, audit_context={"snapshot_json": row.snapshot_json})


async def run_manual_visual_job(user_id: int, job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await visual_job_row(db, user_id, job_id)
        progress = visual_progress(row)
        if row.state == "queued" and progress is not None:
            schedule_visual_job(user_id, job_id, allow_model_steps=not progress.review_only)
