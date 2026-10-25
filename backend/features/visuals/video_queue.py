"""影像制作向公共视频队列提供的任务有效性检查。"""

from typing import Literal

from infrastructure.database import SESSION_LOCAL
from runtime.paid_work import GenerationWorkPaused

from features.media.chain import MediaChainState
from features.media.video_queue.contracts import VideoQueueOwner

from .generation import _require_active_job, visual_progress
from .models import VisualJob


async def inspect_visual_video_owner(
    owner: VideoQueueOwner,
    before_submit: bool = True,
    request_id: str | None = None,
) -> Literal["ready", "pending", "paused", "gone"]:
    async with SESSION_LOCAL() as db:
        job = await db.get(VisualJob, owner.job_id)
        if (
            job is None
            or job.user_id != owner.user_id
            or (job.purpose == "regenerate" and job.target_visual_id is None)
        ):
            return "gone"
        progress = visual_progress(job)
        if progress is None or not progress.video_chain_json or job.state not in {"queued", "running"}:
            return "gone"
        chain = MediaChainState.model_validate_json(progress.video_chain_json)
        if chain.generation_id != owner.generation_id:
            return "gone"
        if request_id is not None and chain.queue_request_id and chain.queue_request_id != request_id:
            return "gone"
        if progress.paused_by_user:
            return "paused"
        if not chain.queue_request_id:
            return "pending"
        if not progress.image_confirmed:
            return "gone"
    if not before_submit:
        return "ready"
    try:
        await _require_active_job(owner.user_id, owner.job_id, chain.generation_id)
    except GenerationWorkPaused:
        return "paused"
    return "ready"
