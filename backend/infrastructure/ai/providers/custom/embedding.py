from typing import ClassVar

from infrastructure.ai.providers.base import ServiceType
from infrastructure.ai.providers.openai_compat import OpenAIEmbeddingProvider


class CustomEmbeddingProvider(OpenAIEmbeddingProvider):
    """标准 OpenAI /embeddings 通用供应商；base_url 填到版本前缀为止（如 https://host/v1），SDK 将拼 /embeddings。

    不请求 dimensions，按模型原生宽度返回；与记忆库列宽不符时由记忆层降级为关键词召回。
    """

    provider_name = "custom"
    service_type = ServiceType.embedding
    DEFAULT_BASE_URL: ClassVar[str] = ""
