"""环境原候选续审，不替换环境图片或提交新生成。"""

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import client_asset_url
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.media.character_images import ImageChainState, review_image_chain
from features.media.image_generation import ImageGenerationError
from features.media.manual_reviews import (
    ManualAction,
    approve_image_chain,
    manual_candidate_views,
    regenerate_image_chain,
)
from features.media.review_contracts import candidate_review_key, needs_review, review_basis
from features.media.review_views import (
    MediaJobView,
    MediaManualRequest,
    MediaReviewRequest,
    MediaSlotView,
    review_request_matches,
)

from .models import SceneJob
from .queries import get_scene
from .service import SceneRegenerationState, _launch_task


def _chain(job: SceneJob) -> ImageChainState:
    return (
        SceneRegenerationState.model_validate(job.checkpoint).image_chain
        if job.purpose == "replace"
        else ImageChainState.model_validate(job.checkpoint or {})
    )


async def scene_review_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(select(SceneJob).where(SceneJob.id == job_id, SceneJob.user_id == user_id))
        if job is None:
            raise RequestError(status_code=404, message="找不到环境检查任务")
        chain = _chain(job)
        candidate = chain.best() or next(iter(chain.candidates), None)
        report = candidate.review if candidate else None
        key = candidate_review_key(chain.generation_id)
        basis = review_basis(report, candidate.evidence if candidate else None, key if candidate else None)
        return MediaJobView(
            id=job.id,
            kind=job.kind,
            state=job.state,
            stage=job.stage,
            revision=job.revision,
            required_slots=1,
            ready_slots=int(report is not None and report.verdict == "pass"),
            complete=job.state == "succeeded",
            slots=[
                MediaSlotView(
                    slot=0,
                    media_id=str(job.id),
                    status=job.stage,
                    review=report,
                    basis_id=basis,
                    candidate_basis_id=key,
                    preview_url=client_asset_url(candidate.path) if candidate else None,
                    can_review=job.stage == "review_pending" and candidate is not None and needs_review(report),
                    error=job.error,
                    candidates=await manual_candidate_views(chain.candidates),
                    can_regenerate=chain.inputs is not None and job.state in {"paused", "failed"},
                )
            ],
        )


async def review_scene_job(user_id: int, job_id: int, slot: int, expected: MediaReviewRequest) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(SceneJob).where(SceneJob.id == job_id, SceneJob.user_id == user_id).with_for_update()
        )
        if job is None or slot != 0 or not review_request_matches(await scene_review_view(user_id, job_id), expected):
            raise RequestError(status_code=409, message="环境检查状态已变化")
        chain = _chain(job)
        job.state, job.stage = "running", "reviewing"
        await db.commit()

    async def save(value: ImageChainState) -> None:
        async with SESSION_LOCAL() as db:
            current = await db.get(SceneJob, job_id, with_for_update=True)
            if current.state != "running":
                raise ValueError("环境检查已停止")
            if current.purpose == "replace":
                frozen = SceneRegenerationState.model_validate(current.checkpoint)
                frozen.image_chain = value
                current.checkpoint = frozen.model_dump(mode="json")
            else:
                current.checkpoint = value.model_dump(mode="json")
            await db.commit()

    try:
        await review_image_chain(user_id, chain, task=AiTaskKey.scene, save_progress=save)
    except ImageGenerationError:
        pass
    finally:
        async with SESSION_LOCAL() as db:
            current = await db.get(SceneJob, job_id, with_for_update=True)
            if current.state == "running":
                current.state, current.stage = (
                    "paused",
                    "review_pending" if chain.can_retry_goal_review() else "confirmation",
                )
                emit_ws_event(
                    db,
                    user_id=user_id,
                    event_type="companion.scene.updated",
                    payload={"scene_id": str(current.scene_id)},
                )
                emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
                await db.commit()
    return await scene_review_view(user_id, job_id)


async def manually_review_scene(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    job = await db.get(SceneJob, job_id)
    scene = await get_scene(db, user_id, job.scene_id) if job.scene_id else None
    if request.slot != 0 or scene is None or job_id not in {scene.initial_job_id, scene.replacement_job_id}:
        raise RequestError(status_code=409, message="原环境任务已失效")
    chain = _chain(job)
    if request.action == "approve":
        await approve_image_chain(user_id, chain, request, db=db)
    else:
        chain = regenerate_image_chain(chain)
    if job.purpose == "replace":
        frozen = SceneRegenerationState.model_validate(job.checkpoint)
        frozen.image_chain = chain
        if request.action == "regenerate":
            frozen.wallpaper_path = ""
        job.checkpoint = frozen.model_dump(mode="json")
    else:
        job.checkpoint = chain.model_dump(mode="json")
    job.availability = "pending"
    job.state, job.stage, job.error = "queued", "evaluating", None
    return ManualAction(restart_job_id=job_id)


async def run_manual_scene_job(user_id: int, job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.get(SceneJob, job_id)
        if job is not None and job.user_id == user_id and job.state == "queued" and job.scene_id:
            _launch_task(job.scene_id, user_id, regeneration_task_id=job.token if job.purpose == "replace" else None)
