from typing import ClassVar

from ..base import ChatProvider


class GrokChatProvider(ChatProvider):
    """通过 xAI /v1/responses 提供 chat；Bearer token 鉴权，上下文 500k。"""

    provider_name = "grok"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.x.ai/v1"
    DEFAULT_MODEL: ClassVar[str] = "grok-4.5"
    CONTEXT_TOKENS: ClassVar[int] = 500_000
    supports_json_object: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high", "xhigh"})
