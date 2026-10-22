"""业务模块公开的只读快照，持久模型留在所有者内部。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AvatarSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: int
    prompt_json: str
    asset_url: str
    seed_fullbody_url: str
    is_fullbody_confirmed: bool
    active: bool
    created_at: datetime
    id: int


class OutfitSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: int
    name: str
    description: str | None
    description_status: str
    description_error: str | None
    fullbody_url: str
    status: str
    source_json: str
    is_initial: bool
    active: bool
    id: int
    created_at: datetime
    updated_at: datetime
