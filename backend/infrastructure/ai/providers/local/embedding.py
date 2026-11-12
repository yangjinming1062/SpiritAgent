from dataclasses import replace
from math import hypot, isfinite
from typing import ClassVar

from infrastructure.ai.providers.base import ProviderConfig, ProviderError, ServiceType
from infrastructure.ai.providers.openai_compat import OpenAIEmbeddingProvider


class LocalEmbeddingProvider(OpenAIEmbeddingProvider):
    """自托管 embeddings；验证维度、归一化，并按模型补查询指令。"""

    provider_name = "local"
    service_type = ServiceType.embedding
    # 默认 LM Studio。
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:1234/v1"
    requires_api_key: ClassVar[bool] = False
    # Qwen3-Embedding 官方要求查询带任务指令、文档不带；按模型名启用，其他自托管模型保持原文。
    QUERY_INSTRUCTION_MODEL: ClassVar[str] = "qwen3-embedding"
    QUERY_INSTRUCTION: ClassVar[str] = (
        "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
    )

    def __init__(self, config: ProviderConfig) -> None:
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)

    def _query_inputs(self, texts: list[str], purpose: str) -> list[str]:
        if purpose != "query" or self.QUERY_INSTRUCTION_MODEL not in self.config.model.lower():
            return texts
        return [self.QUERY_INSTRUCTION + text for text in texts]

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        vectors = await super().embed(self._query_inputs(texts, purpose), purpose=purpose)
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
