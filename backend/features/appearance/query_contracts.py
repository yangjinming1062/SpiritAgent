"""业务模块公开的只读快照，持久模型留在所有者内部。"""

from datetime import datetime
from typing import Any

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict


class AvatarSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: ResourceId
    prompt_json: dict[str, Any]
    asset_url: str
    seed_fullbody_url: str
    is_fullbody_confirmed: bool
    active: bool
    created_at: datetime
    id: ResourceId


class OutfitSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: ResourceId
    name: str
    description: str | None
    description_status: str
    description_error: str | None
    fullbody_url: str
    status: str
    source_json: dict[str, Any]
    is_initial: bool
    id: ResourceId
    created_at: datetime
    updated_at: datetime
