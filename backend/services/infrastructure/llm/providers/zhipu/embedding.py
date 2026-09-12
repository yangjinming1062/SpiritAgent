from typing import ClassVar

from ..base import ServiceType
from ..openai_compat import OpenAIEmbeddingProvider


class ZhipuEmbeddingProvider(OpenAIEmbeddingProvider):
    """通过 Zhipu 的 OpenAI 兼容 /embeddings 端点提供 embeddings，默认模型 embedding-3。"""

    provider_name = "zhipu"
    service_type = ServiceType.embedding
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"embedding": "embedding-3"}
