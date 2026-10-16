"""桌面制作向公共视频队列提供的任务有效性检查。"""

from typing import Literal

from infrastructure.database import SESSION_LOCAL
from runtime.paid_work import GenerationWorkPaused

from features.desktop_life.generation import _require_active_job
from features.desktop_life.models import DesktopVideoAction
from features.desktop_life.repository import desktop_progress
from features.media.chain import MediaChainState
from features.media.video_queue.contracts import VideoQueueOwner


async def inspect_video_queue_owner(
    owner: VideoQueueOwner,
    before_submit: bool = True,
    request_id: str | None = None,
) -> Literal["ready", "pending", "paused", "gone"]:
    async with SESSION_LOCAL() as db:
        action = await db.get(DesktopVideoAction, owner.entity_id)
        if action is None or action.user_id != owner.user_id:
            return "gone"
        progress = desktop_progress(action)
        if progress is None or not progress.video_chain_json or action.status != "processing":
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
        await _require_active_job(owner.user_id, owner.entity_id, progress.generation_id)
    except GenerationWorkPaused:
        return "paused"
    return "ready"
