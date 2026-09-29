from dataclasses import replace
from typing import ClassVar

from ..base import ChatProvider, ProviderConfig


class LocalChatProvider(ChatProvider):
    """自托管 Responses 服务；配置与部署约束见 Backend README。"""

    provider_name = "local"
    # 默认 LM Studio；模型须显式填写已部署的模型 ID。
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:1234/v1"
    # 保守预算；服务端加载窗口须至少覆盖此值。
    CONTEXT_TOKENS: ClassVar[int] = 16_000
    requires_api_key: ClassVar[bool] = False
    # 不同本地模型的格式与推理参数支持不一致，不统一声明。
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: ProviderConfig) -> None:
        # LM Studio 默认不校验 Authorization；空密钥用占位符以满足 OpenAI SDK 构造要求。
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)
