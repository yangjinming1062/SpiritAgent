"""环境原候选续审，不替换环境图片或提交新生成。"""

from infrastructure.assets.asset_store import client_asset_url
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.media.character_images import ImageChainState
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
    single_slot_review_view,
)

from .generation_state import scene_generation_state
from .models import SceneJob
from .queries import get_scene
from .service import _launch_task


def _chain(job: SceneJob) -> ImageChainState:
    return scene_generation_state(job).image_chain


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
        return single_slot_review_view(
            job,
            media_id=str(job.id),
            review=report,
            basis_id=basis,
            candidate_key=key,
            preview_url=client_asset_url(candidate.path) if candidate else None,
            can_review=job.stage == "review_pending" and candidate is not None and needs_review(report),
            candidates=await manual_candidate_views(chain.candidates),
            error=job.error,
            can_regenerate=chain.inputs is not None and job.state in {"paused", "failed"},
        )


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
    frozen = scene_generation_state(job)
    frozen.image_chain = chain
    if request.action == "regenerate":
        frozen.wallpaper_path = ""
    job.checkpoint = frozen.model_dump(mode="json")
    if job.purpose == "create":
        scene.status = "pending"
    job.state, job.stage, job.error = "queued", "evaluating", None
    return ManualAction(restart_job_id=job_id)


async def run_manual_scene_job(user_id: int, job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.get(SceneJob, job_id)
        if job is not None and job.user_id == user_id and job.state == "queued" and job.scene_id:
            _launch_task(job.scene_id, user_id, regeneration_task_id=job.token if job.purpose == "replace" else None)
