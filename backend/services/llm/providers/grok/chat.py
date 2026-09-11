from typing import ClassVar

from ..base import ServiceType
from ..openai_responses import OpenAIResponsesChatProvider


class GrokChatProvider(OpenAIResponsesChatProvider):
    """通过 xAI /v1/responses 提供 chat；Bearer token 鉴权，上下文 500k。"""

    provider_name = "grok"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "grok-4.5"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 500_000}
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high"})
    # 按 OpenAI 兼容默认 [0.0, 2.0]
    TEMPERATURE_MAX: ClassVar[float] = 2.0
