from typing import ClassVar

from ..base import ServiceType
from ..openai_responses import OpenAIResponsesChatProvider


class QwenChatProvider(OpenAIResponsesChatProvider):
    """通过千问 OpenAI Responses 兼容端点提供 chat，默认 qwen3.8-max。"""

    provider_name = "qwen"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "qwen3.8-max"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 256_000}
    supports_vision: ClassVar[bool] = True
    supports_json_object: ClassVar[bool] = True
    supports_json_array: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
    # 文档取值范围 [0, 2)
    TEMPERATURE_MAX: ClassVar[float] = 2.0
