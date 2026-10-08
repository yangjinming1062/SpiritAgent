from typing import ClassVar

from ..base import ChatProvider


class QwenChatProvider(ChatProvider):
    """通过千问 OpenAI Responses 兼容端点提供 chat，默认 qwen3.8-max。"""

    provider_name = "qwen"
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    DEFAULT_MODEL: ClassVar[str] = "qwen3.8-max"
    supports_vision: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
