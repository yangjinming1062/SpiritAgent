from typing import ClassVar

from ..base import ServiceType
from ..openai_responses import OpenAIResponsesChatProvider


class MiniMaxChatProvider(OpenAIResponsesChatProvider):
    """通过 MiniMax /v1/responses 提供 chat；M3 支持 image_url 输入，M2.x 仅文本。"""

    provider_name = "minimax"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-M3"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    # 文本与视觉共用 M3。
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "MiniMax-M3"}
    # M3 的 /v1/responses 接受扁平 input_video（data URL 实测 49MB 可用；容器仅 mp4/mov，webm 被拒）。
    supports_video: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high"})
    # MiniMax /v1/responses 文档支持区间 (0, 1.0]，下限垫 0.01
    TEMPERATURE_MIN: ClassVar[float] = 0.01
    TEMPERATURE_MAX: ClassVar[float] = 1.0
