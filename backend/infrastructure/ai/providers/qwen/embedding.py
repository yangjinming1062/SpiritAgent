from typing import ClassVar

from infrastructure.ai.providers.base import ServiceType
from infrastructure.ai.providers.openai_compat import OpenAIEmbeddingProvider


class QwenEmbeddingProvider(OpenAIEmbeddingProvider):
    """通过千问 OpenAI 兼容 /embeddings 提供 embeddings；使用供应商原生输出维度。"""

    provider_name = "qwen"
    service_type = ServiceType.embedding
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/compatible-mode/v1"
