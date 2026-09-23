"""视频包的持久化生成上下文与单动作处理结果。"""

from typing import Literal

from modules.companion import CharacterCardSnapshot
from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.video_processing import FullClipWindow, LoopWindow

from ..character_images import ImageChainState
from .manifest import VideoClipSpec


class GenerationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: CharacterCardSnapshot
    identity_reference_path: str
    reference_chain: ImageChainState = Field(default_factory=ImageChainState)
    reference_alignment: Literal["pending", "running", "ready"]
    persona_definition: dict[str, str]
    personality_tags: list[str]
    outfit_description: str
    feedback: str
    action_feedback: dict[str, str] = Field(default_factory=dict)
    active_outfit_id: int | None
    # 本版本必须成功的动作。
    must_actions: list[str]


class ActionResult(BaseModel):
    """单动作处理结果；quality 为 loop 接点窗口或 once 完整窗口。"""

    model_config = ConfigDict(extra="forbid")

    clip: VideoClipSpec
    cover_path: str
    hitmask_path: str = ""
    quality: LoopWindow | FullClipWindow | None = None
