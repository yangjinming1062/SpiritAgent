from dataclasses import replace
from math import hypot, isfinite
from typing import ClassVar

from infrastructure.ai.providers.base import ProviderConfig, ProviderError, ServiceType
from infrastructure.ai.providers.openai_compat import OpenAIEmbeddingProvider


class LocalEmbeddingProvider(OpenAIEmbeddingProvider):
    """自托管 embeddings；验证原生维度并归一化。"""

    provider_name = "local"
    service_type = ServiceType.embedding
    # 默认 LM Studio。
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:1234/v1"
    requires_api_key: ClassVar[bool] = False

    def __init__(self, config: ProviderConfig) -> None:
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        vectors = await super().embed(texts, purpose=purpose)
        if not vectors:
            return []
        width = len(vectors[0]) if isinstance(vectors[0], list) else 0
        if not 0 < width <= 16000 or any(
            not isinstance(vector, list)
            or len(vector) != width
            or any(
                isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value) for value in vector
            )
            for vector in vectors
        ):
            raise ProviderError("local embedding response contains invalid or inconsistent vectors")
        result: list[list[float]] = []
        for vector in vectors:
            norm = hypot(*vector)
            if not norm or not isfinite(norm):
                raise ProviderError("local embedding cannot be normalized")
            result.append([value / norm for value in vector])
        return result
