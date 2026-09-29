from dataclasses import replace
from math import hypot, isfinite
from typing import ClassVar

from modules.memory import MEMORY_EMBEDDING_DIM

from ..base import ProviderConfig, ProviderError, ServiceType
from ..openai_compat import OpenAIEmbeddingProvider


class LocalEmbeddingProvider(OpenAIEmbeddingProvider):
    """自托管 embeddings；按模型能力适配记忆库列宽。"""

    provider_name = "local"
    service_type = ServiceType.embedding
    # 默认 LM Studio。
    DEFAULT_BASE_URL: ClassVar[str] = "http://127.0.0.1:1234/v1"
    DEFAULT_MODEL: ClassVar[str] = "qwen3-embedding-4b"
    requires_api_key: ClassVar[bool] = False
    # 默认模型（Qwen3-Embedding-4B）原生宽度；支持 MRL 截断到记忆库列宽。
    MRL_NATIVE_WIDTH: ClassVar[int] = 2560

    def __init__(self, config: ProviderConfig) -> None:
        if not config.api_key:
            config = replace(config, api_key="local")
        super().__init__(config)

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        vectors = await super().embed(texts, purpose=purpose)
        if not vectors:
            return []
        width = len(vectors[0]) if isinstance(vectors[0], list) else 0
        # 仅默认模型确认支持 MRL；其他模型超宽拒绝，短向量仍可补零。
        max_width = self.MRL_NATIVE_WIDTH if self.config.model == self.DEFAULT_MODEL else MEMORY_EMBEDDING_DIM
        if not 0 < width <= max_width or any(
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
            values = vector[:MEMORY_EMBEDDING_DIM]
            norm = hypot(*values)
            if not norm or not isfinite(norm):
                raise ProviderError("local embedding cannot be normalized at the memory dimension")
            result.append([value / norm for value in values] + [0.0] * (MEMORY_EMBEDDING_DIM - len(values)))
        return result
