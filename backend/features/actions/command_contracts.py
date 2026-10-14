"""制作命令对外返回只读记录，持久模型留在动作模块。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ActionPackSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
    user_id: int
    avatar_id: int | None
    outfit_id: int | None
    reference_path: str
    reference_hash: str
    character_snapshot: str
    outfit_snapshot: str
    context_json: str | None
    canvas_spec: str
    catalog_version: int
    manifest_path: str
    cover_path: str | None
    content_hash: str | None
    identity_review: str
    identity_review_reason: str
    status: str
    active: bool
    appearance_epoch: int
    error: str | None


class ActionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
    user_id: int
    pack_id: int
    key: str
    name: str
    system_slot: str
    media_type: str
    kind: str | None
    motion_description: str
    use_when: str
    avoid_when: str
    enabled: bool
    metadata_revision: int
    status: str
    stage: str
    generation_state_json: str | None
    pose_generation_state_json: str | None
    provider: str
    model: str | None
    provider_task_id: str | None
    reference_hash: str | None
    outfit_id: int | None
    error: str | None
    artifact_path: str | None
    pose_path: str | None
    script_json: str | None
    result_json: str | None
    accepted_asset_json: str | None
    source_design_json: str | None
    media_path: str
    media_hash: str
    target_duration_seconds: float | None
    actual_duration_ms: int | None
    frames: int | None
    loopable: bool | None
    cover_path: str | None
    hitmask_path: str | None
    hitmask_grid_w: int | None
    hitmask_grid_h: int | None
    hitmask_fps: int | None
    peek_geometry_json: str | None
    content_rect_json: str | None
