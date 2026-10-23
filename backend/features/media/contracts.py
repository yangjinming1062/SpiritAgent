"""媒体模块公开快照；调用方不持有制作任务的 ORM。"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .video_queue.contracts import VideoQueueInfo


class VideoJobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    user_id: int
    session_id: str | None
    source_message_id: int | None
    structured_reply: bool
    media_id: str | None
    reply_message_id: int | None
    status: str
    video_url: str | None
    candidate_video_url: str | None
    error_reason: str | None
    error_message: str | None
    cleanup_requested_at: datetime | None
    candidate_ready: bool
    video_queue: VideoQueueInfo | None = None


class VideoJobAssetReferences(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    video_url: str | None
    candidate_video_url: str | None
    params_json: str
    generation_state_json: str


class ReleasedVideoJobs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_ids: frozenset[int]
    references: tuple[VideoJobAssetReferences, ...]


@dataclass(frozen=True)
class RemovedVideoTask:
    """已放弃制作的视频队列状态；本地事务提交后尽力撤销。"""

    task_id: int
    generation_state_json: str | None
