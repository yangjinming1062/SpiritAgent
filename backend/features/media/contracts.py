"""媒体模块公开快照；调用方不持有制作任务的 ORM。"""

from datetime import datetime

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict

from .review_contracts import MediaSelection, VisualReview
from .video_queue.contracts import VideoQueueInfo


def video_delivery_error(status: str) -> str | None:
    if status in {"unknown", "result_unknown"}:
        return "视频制作结果尚未收到"
    return "本次视频未完成" if status in {"failed", "discarded", "cancelled"} else None


class VideoJobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ResourceId
    user_id: ResourceId
    revision: int = 0
    session_id: str | None
    request_scope: str | None = None
    source_message_id: ResourceId | None
    structured_reply: bool
    media_id: str | None
    reply_message_id: ResourceId | None
    status: str
    video_url: str | None
    error_reason: str | None
    error_message: str | None
    cleanup_requested_at: datetime | None
    receive_only: bool = False
    review: VisualReview | None = None
    selection: MediaSelection | None = None
    video_queue: VideoQueueInfo | None = None


class VideoJobAssetReferences(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    video_url: str | None
    candidate_video_url: str | None
    params_json: dict
    generation_state_json: dict


class ReleasedVideoJobs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_ids: frozenset[ResourceId]
    references: tuple[VideoJobAssetReferences, ...]
