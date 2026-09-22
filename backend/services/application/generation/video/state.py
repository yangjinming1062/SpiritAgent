"""视频包的持久化生成上下文与单动作处理结果。"""

from typing import Literal

from modules.companion import CharacterCardSnapshot
from pydantic import BaseModel, ConfigDict

from services.infrastructure.video_processing.quality import FullClipWindow, LoopWindow

from .manifest import VideoClipSpec


class GenerationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: CharacterCardSnapshot
    reference_alignment: Literal["pending", "running", "ready"]
    persona_definition: dict[str, str]
    personality_tags: list[str]
    outfit_description: str
    feedback: str
    active_outfit_id: int | None
    # 本版本必须成功的动作；空表示旧数据，按全部任务判定。
    must_actions: list[str] = []


class ActionResult(BaseModel):
    """单动作处理结果；quality 为 loop 接点窗口或 once 完整窗口。"""

    model_config = ConfigDict(extra="forbid")

    clip: VideoClipSpec
    cover_path: str
    quality: LoopWindow | FullClipWindow | None = None
