"""业务模块公开的只读快照，持久模型留在所有者内部。"""

from datetime import datetime

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict


class SceneSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: ResourceId
    status: str
    can_retry_generation: bool = False
    stage: str
    origin: str
    source: str
    target_size_json: dict | None
    source_size_json: dict | None
    image_size_json: dict | None
    title: str
    description: str
    requirements: str
    prompt: str
    media_path: str
    upload_source_path: str
    regeneration_status: str | None
    regeneration_stage: str | None
    regeneration_error: str | None
    regeneration_task_id: str | None
    reference_image: str
    error: str | None
    attempt_count: int
    requested_at: datetime
    ready_at: datetime | None
    id: ResourceId
    created_at: datetime
    updated_at: datetime


class SceneSnapshotPage(BaseModel):
    scenes: list[SceneSnapshot]
    has_more: bool
    next_cursor: str | None = None
    total: int | None = None
    limit: int
