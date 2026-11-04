"""媒体模块公开快照；调用方不持有制作任务的 ORM。"""

from datetime import datetime

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict

from .review_contracts import VisualReview
from .video_queue.contracts import VideoQueueInfo


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
    candidate_video_url: str | None
    error_reason: str | None
    error_message: str | None
    cleanup_requested_at: datetime | None
    review: VisualReview | None = None
    candidate_ready: bool
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
