from typing import ClassVar

from infrastructure.ai.providers.base import ChatProvider


class QwenChatProvider(ChatProvider):
    """通过千问 OpenAI Responses 兼容端点提供模型卡指定的 chat。"""

    supports_audio: ClassVar[bool] = True
    supports_video: ClassVar[bool] = True
    provider_name = "qwen"
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    supports_image: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
