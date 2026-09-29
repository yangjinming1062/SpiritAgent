from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAIEmbeddingProvider


class QwenEmbeddingProvider(OpenAIEmbeddingProvider):
    """通过千问 OpenAI 兼容 /embeddings 提供 embeddings，默认 text-embedding-v4（1024 维）。"""

    provider_name = "qwen"
    service_type = ServiceType.embedding
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    DEFAULT_MODEL: ClassVar[str] = "text-embedding-v4"
