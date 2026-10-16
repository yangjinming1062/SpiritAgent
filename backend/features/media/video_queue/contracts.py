"""视频队列的公开归属、进度和管理契约。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

type VideoQueueKind = Literal["media", "action", "desktop"]
type VideoQueueReadiness = Literal["ready", "pending", "paused", "gone"]


class VideoQueueOwner(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: VideoQueueKind
    user_id: int
    entity_id: int
    generation_id: str


class VideoQueueInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["waiting", "rate_limited", "blocked"]
    next_retry_at: datetime | None = None


class VideoQueueStoppedError(RuntimeError):
    """原尝试已明确结束，不能自动派生另一笔付费制作。"""

    can_fallback = False
    reason = "queue_stopped"


class VideoQueueRequestView(BaseModel):
    id: str
    revision: int
    kind: VideoQueueKind
    entity_id: int | None
    user_id: int | None
    model: str
    state: str
    provider_task_id: str | None
    created_at: datetime
    updated_at: datetime
    error_message: str | None


class VideoQueueLaneView(BaseModel):
    id: str
    provider: str
    provider_names: list[str]
    next_retry_at: datetime | None
    requests: list[VideoQueueRequestView]


class VideoQueueList(BaseModel):
    queues: list[VideoQueueLaneView]


class VideoQueueResolutionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=1000)


class VideoQueueAttachTask(VideoQueueResolutionBase):
    action: Literal["attach_task"]
    task_id: str = Field(min_length=1, max_length=128)


class VideoQueueConfirmFinished(VideoQueueResolutionBase):
    action: Literal["confirm_finished"]
    task_id: None = None


type VideoQueueResolution = Annotated[
    VideoQueueAttachTask | VideoQueueConfirmFinished,
    Field(discriminator="action"),
]
