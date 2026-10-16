"""视频队列的公开归属、进度和管理契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class VideoQueueResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_revision: int = Field(ge=0)
    action: Literal["attach_task", "confirm_finished"]
    task_id: str | None = Field(default=None, max_length=128)
    note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_task_id(self) -> "VideoQueueResolution":
        if self.action == "attach_task" and not self.task_id:
            raise ValueError("补录任务时必须填写任务 ID")
        if self.action == "confirm_finished" and self.task_id:
            raise ValueError("确认结束不接受任务 ID")
        return self
