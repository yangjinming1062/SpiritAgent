"""业务模块公开的只读快照，持久模型留在所有者内部。"""

from datetime import datetime

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict

from .definition import visual_persona_definition


class PersonaSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: ResourceId
    definition_json: dict[str, str]
    personality_tags_json: list[str]
    is_complete: bool
    is_portrait_confirmed: bool
    portrait_confirmed_at: datetime | None
    scene_policy: str
    outfit_policy: str
    current_mood: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def performance_context(self) -> dict[str, str]:
        traits = visual_persona_definition(self.definition_json)
        if not traits and self.personality_tags_json:
            traits["personality"] = "、".join(self.personality_tags_json)
        if self.current_mood:
            traits["current_mood"] = self.current_mood
        return traits
