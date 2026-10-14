from typing import ClassVar

from infrastructure.ai.providers.base import ChatProvider


class GrokChatProvider(ChatProvider):
    """通过 xAI /v1/responses 提供 chat；模型能力和预算取能力卡片。"""

    provider_name = "grok"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.x.ai/v1"
    supports_image: ClassVar[bool] = True
    supports_json_object: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high", "xhigh"})
