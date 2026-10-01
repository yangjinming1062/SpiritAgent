"""视频包的持久化生成上下文与单动作处理结果。"""

from typing import Literal

from modules.companion import CharacterCardSnapshot, PeekGeometry
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
    feedback: str
    action_feedback: dict[str, str] = Field(default_factory=dict)
    active_outfit_id: int | None
    # 本版本必须成功的动作。
    must_actions: list[str]


class VideoClipSpec(BaseModel):
    """单个动作交付片段：不可变资源与计时；命中遮罩单独落盘。"""

    model_config = ConfigDict(extra="forbid")

    action: str
    path: str
    sha256: str
    frames: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    peek_geometry: PeekGeometry | None = None
    content_rect: tuple[float, float, float, float] | None = None


class ActionResult(BaseModel):
    """单动作处理结果：交付片段与独立落盘的封面、命中遮罩。"""

    model_config = ConfigDict(extra="forbid")

    clip: VideoClipSpec
    cover_path: str
    hitmask_path: str
