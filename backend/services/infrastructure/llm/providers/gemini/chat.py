from dataclasses import replace
from typing import ClassVar
from urllib.parse import urlsplit

from ..base import ChatProvider, ProviderConfig


class GeminiChatProvider(ChatProvider):
    """经 Gemini 兼容网关的 /v1/responses 提供 chat；卡片端点按裸主机保存，与 embedding / image 共用。

    官方 Gemini API 只提供 OpenAI chat.completions 兼容层、没有 /responses 端点，须经补全该端点的
    网关（如本地代理）使用；端点未含路径时补 /v1，显式填写的路径原样使用。
    """

    provider_name = "gemini"
    DEFAULT_BASE_URL: ClassVar[str] = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL: ClassVar[str] = "gemini-3.8-flash"
    CONTEXT_TOKENS: ClassVar[int] = 1_000_000
    supports_json_object: ClassVar[bool] = True
    supports_vision: ClassVar[bool] = True
    # 兼容无鉴权网关；公共 API 仍需在卡片填写密钥，缺失时按鉴权错误失败。
    requires_api_key: ClassVar[bool] = False

    def __init__(self, config: ProviderConfig) -> None:
        # chat SDK 拼接 /responses，Gemini 兼容层的 OpenAI 端点位于 /v1。
        base_url = config.base_url.rstrip("/")
        if not urlsplit(base_url).path:
            config = replace(config, base_url=base_url + "/v1")
        if not config.api_key:
            # OpenAI SDK 构造要求非空密钥；无鉴权网关用占位值。
            config = replace(config, api_key="gemini")
        super().__init__(config)
