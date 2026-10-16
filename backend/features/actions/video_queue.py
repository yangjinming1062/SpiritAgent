"""动作制作向公共视频队列提供的有效性检查。"""

from typing import Literal

from infrastructure.database import SESSION_LOCAL
from runtime.paid_work import GenerationWorkPaused
from sqlalchemy import select

from features.actions.generation.queries import _load_generation_context
from features.actions.generation.references import _require_new_paid_step, require_action_matting_model
from features.actions.models import CompanionAction, CompanionActionPack
from features.media.chain import MediaChainState
from features.media.video_queue.contracts import VideoQueueOwner


async def inspect_video_queue_owner(
    owner: VideoQueueOwner,
    before_submit: bool = True,
    request_id: str | None = None,
) -> Literal["ready", "pending", "paused", "gone"]:
    async with SESSION_LOCAL() as db:
        job = await db.get(CompanionAction, owner.entity_id)
        if job is None or job.user_id != owner.user_id or not job.generation_state_json:
            return "gone"
        state = MediaChainState.model_validate_json(job.generation_state_json)
        pack = await db.get(CompanionActionPack, job.pack_id)
        if (
            state.generation_id != owner.generation_id
            or job.status not in {"queued", "processing"}
            or pack is None
            or pack.status not in {"processing", "ready"}
        ):
            return "gone"
        if not state.queue_request_id:
            return "pending"
        if request_id is not None and state.queue_request_id != request_id:
            return "gone"
        context = _load_generation_context(pack)
        if context is None:
            return "gone"
    if not before_submit:
        return "ready"
    try:
        await _require_new_paid_step(pack, context)
        require_action_matting_model()
    except GenerationWorkPaused:
        return "paused"
    return "ready"


async def has_untracked_video_attempts() -> bool:
    async with SESSION_LOCAL() as db:
        jobs = await db.scalars(
            select(CompanionAction).where(
                CompanionAction.media_type == "video",
                CompanionAction.generation_state_json.is_not(None),
            ),
        )
        for job in jobs:
            state = MediaChainState.model_validate_json(job.generation_state_json or "{}")
            if (
                not state.queue_request_id
                and not state.result_url
                and not state.source_path
                and not job.artifact_path
                and state.stop_reason not in {"submit_failed", "provider_failed", "content_policy_blocked"}
                and (state.phase == "submitting" or job.provider_task_id)
            ):
                return True
    return False
