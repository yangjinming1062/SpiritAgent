"""业务模块公开的只读快照，持久模型留在所有者内部。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SceneSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: int
    status: str
    stage: str
    origin: str
    source: str
    target_size_json: str | None
    source_size_json: str | None
    image_size_json: str | None
    title: str
    description: str
    requirements: str
    prompt: str
    media_path: str
    upload_source_path: str
    generation_state_json: str | None
    regeneration_status: str | None
    regeneration_stage: str | None
    regeneration_error: str | None
    regeneration_task_id: str | None
    regeneration_state_json: str | None
    reference_image: str
    error: str | None
    attempt_count: int
    requested_at: datetime
    ready_at: datetime | None
    id: int
    created_at: datetime
    updated_at: datetime
