from typing import ClassVar

from infrastructure.ai.providers.base import EmbeddingProvider, ProviderConfig, ProviderError, ServiceType
from infrastructure.ai.providers.http import get_http
from infrastructure.ai.providers.minimax._errors import raise_for_minimax_response


class MiniMaxEmbeddingProvider(EmbeddingProvider):
    """通过 MiniMax 原生 /v1/embeddings HTTP API 提供 embeddings；要求 texts（字符串列表）与 type（"db" 或 "query"），返回 vectors 浮点向量列表（embo-01 维度 1536）。"""

    provider_name = "minimax"
    service_type = ServiceType.embedding
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._http = get_http(config.base_url, config.api_key)

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.config.model, "texts": texts, "type": purpose}
        resp = await self._http.post("/v1/embeddings", json=payload)
        body = raise_for_minimax_response(resp)
        vectors = body.get("vectors")
        if not vectors or not isinstance(vectors, list):
            raise ProviderError("MiniMax embedding returned empty or invalid vectors", status_code=502)
        return vectors
