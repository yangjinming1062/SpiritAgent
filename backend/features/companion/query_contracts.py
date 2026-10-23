"""业务模块公开的只读快照，持久模型留在所有者内部。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PersonaSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: int
    definition_json: str
    personality_tags_json: str
    is_complete: bool
    is_portrait_confirmed: bool
    portrait_confirmed_at: datetime | None
    scene_policy: str
    outfit_policy: str
    current_mood: str | None
    id: int
    created_at: datetime
    updated_at: datetime
