from typing import ClassVar

from ..base import ChatProvider


class MiniMaxChatProvider(ChatProvider):
    """通过 MiniMax /v1/responses 提供 chat；M3 支持 image_url 输入，M2.x 仅文本。"""

    provider_name = "minimax"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "MiniMax-M3"
    CONTEXT_TOKENS: ClassVar[int] = 1_000_000
    # 文本与视觉共用 M3。
    supports_vision: ClassVar[bool] = True
    # M3 的 /v1/responses 接受扁平 input_video（data URL 实测 49MB 可用；容器仅 mp4/mov，webm 被拒）。
    supports_video: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "minimal", "low", "medium", "high"})
    # MiniMax /v1/responses 文档支持区间 (0, 1.0]，下限垫 0.01
    TEMPERATURE_MIN: ClassVar[float] = 0.01
    TEMPERATURE_MAX: ClassVar[float] = 1.0
