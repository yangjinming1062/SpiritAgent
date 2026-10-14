from collections.abc import Awaitable, Callable
from typing import ClassVar

from openai import AsyncOpenAI

from infrastructure.ai.providers.base import ChatProvider


class DeepSeekChatProvider(ChatProvider):
    """通过 DeepSeek 原生 Responses 提供对话；模型能力和预算取能力卡片。"""

    provider_name = "deepseek"
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.deepseek.com"
    supports_image: ClassVar[bool] = True
    supports_json_object: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "high", "max"})

    def structured_response_client(
        self,
        *,
        repair: bool,
        before_submit: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncOpenAI:
        # 原生 Responses 支持 JSON Schema；首次提交的前置检查由调用方执行。
        return self.raw_client()
