"""动作包的持久化生成上下文。"""

from typing import Literal

from modules.companion import CharacterCardSnapshot
from pydantic import BaseModel, ConfigDict, Field

from ..character_images import ImageChainState


class GenerationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: CharacterCardSnapshot
    identity_reference_path: str
    reference_chain: ImageChainState = Field(default_factory=ImageChainState)
    reference_alignment: Literal["pending", "running", "ready"]
    persona_definition: dict[str, str]
    personality_tags: list[str]
    outfit_description: str
    # 默认外观可等待已受理描述，在首次模型调用前冻结一次；旧包保持既有快照。
    outfit_description_frozen: bool = True
    feedback: str
    action_feedback: dict[str, str] = Field(default_factory=dict)
    active_outfit_id: int | None
    # 本版本必须成功的动作。
    must_actions: list[str]
