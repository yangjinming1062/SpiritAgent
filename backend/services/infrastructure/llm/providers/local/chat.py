from dataclasses import replace
from typing import ClassVar

from ..base import ProviderConfig, ServiceType
from ..openai_responses import OpenAIResponsesChatProvider


class LocalChatProvider(OpenAIResponsesChatProvider):
    """自托管 Responses 服务；配置与部署约束见 Backend README。"""

    provider_name = "local"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": ""}
    # 保守预算；服务端加载窗口须至少覆盖此值。
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 16_000}
    requires_api_key: ClassVar[bool] = False
    # 不同本地模型的格式与推理参数支持不一致，不统一声明。
    supports_json_object: ClassVar[bool] = False
    supports_json_array: ClassVar[bool] = False
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: ProviderConfig) -> None:
        # LM Studio 默认不校验 Authorization；空密钥用占位符以满足 OpenAI SDK 构造要求。
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)
