from typing import ClassVar

from ..base import ServiceType
from ..openai_responses import OpenAIResponsesChatProvider


class MiMoChatProvider(OpenAIResponsesChatProvider):
    provider_name = "mimo"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.5-pro"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    # 视觉用 mimo-v2.5（而非文生 mimo-v2.5-pro），共用同一 base_url；token-plan key 限定在 token-plan-cn 主机，该主机提供 mimo-v2.5。
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.5"}
    # 不声明 supports_video：mimo 视频理解仅走 chat.completions 的嵌套 video_url（base64≤50MB/URL≤300MB），
    # token-plan 的 /v1/responses 网关拒绝 input_video（"input item type 'input_video' is not supported"），
    # 而 chat 管线是 Responses-only；如需 mimo 进视频回退链需另建协议适配。
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high"})
    # MiMo 文档支持温度区间 [0.0, 1.5]
    TEMPERATURE_MAX: ClassVar[float] = 1.5
