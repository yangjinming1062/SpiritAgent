"""全身候选的人工采用沿用原头像资格与确定性文件校验。"""

from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from sqlalchemy.ext.asyncio import AsyncSession

from features.media.chain import MediaCandidate
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.manual_reviews import ManualAction, approve_candidate, manual_candidate_id
from features.media.review_views import MediaManualRequest

from .avatar_service import AvatarGenerationError, accept_fullbody_candidate
from .background_tasks import schedule_appearance_tasks
from .identity import visual_identity_is_current
from .models import AvatarAsset, FullbodyCandidate, FullbodyReviewJob


async def manually_review_fullbody(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    job = await db.get(FullbodyReviewJob, job_id)
    candidate = await db.get(FullbodyCandidate, job.candidate_id, with_for_update=True) if job.candidate_id else None
    if (
        candidate is None
        or candidate.status not in {"pending", "failed", "ready"}
        or request.slot != 0
        or request.action != "approve"
    ):
        raise RequestError(status_code=409, message="请使用全身参考的重新生成操作")
    avatar = await db.get(AvatarAsset, candidate.avatar_id)
    identity = VisualIdentitySnapshot(avatar_id=candidate.avatar_id, revision=candidate.base_revision)
    if (
        avatar is None
        or candidate.base_fullbody_url != avatar.seed_fullbody_url
        or not await visual_identity_is_current(db, user_id, identity)
    ):
        raise RequestError(status_code=409, message="原任务身份已失效，不能按当前身份采纳")
    selected = MediaCandidate(path=candidate.image_url, attempt=0)
    if manual_candidate_id(selected) != request.candidate_id:
        raise RequestError(status_code=409, message="所选候选已变化，请刷新")
    await approve_candidate(user_id, selected, ("identity",), request, db=db)
    candidate.review_json = {**candidate.review_json, "manual_review": selected.review.model_dump(mode="json")}
    candidate.status, candidate.error = "ready", None
    job.state, job.stage, job.error = "queued", "confirmation", None
    return ManualAction(restart_job_id=job_id)


async def run_manual_fullbody_job(user_id: int, job_id: int) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.get(FullbodyReviewJob, job_id)
        if job is None or job.user_id != user_id or job.state != "queued":
            return
        candidate = await db.get(FullbodyCandidate, job.candidate_id) if job.candidate_id else None
        if candidate is None:
            return
        avatar_id, candidate_id = candidate.avatar_id, candidate.id
    try:
        await accept_fullbody_candidate(user_id, avatar_id, candidate_id)
        schedule_appearance_tasks(user_id)
    except AvatarGenerationError as exc:
        async with SESSION_LOCAL() as db:
            job = await db.get(FullbodyReviewJob, job_id, with_for_update=True)
            if job and job.state == "queued":
                job.state, job.stage, job.error = "paused", "confirmation", str(exc)
                candidate = await db.get(FullbodyCandidate, candidate_id)
                if candidate:
                    candidate.error = str(exc)
                await db.commit()
