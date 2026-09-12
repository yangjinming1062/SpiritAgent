from typing import ClassVar

from ..base import EmbeddingProvider, ProviderConfig, ProviderError, ServiceType
from ..http import get_http
from ._errors import raise_for_minimax_response


class MiniMaxEmbeddingProvider(EmbeddingProvider):
    """通过 MiniMax 原生 /v1/embeddings HTTP API 提供 embeddings；要求 texts（字符串列表）与 type（"db" 或 "query"），返回 vectors 浮点向量列表（embo-01 维度 1536）。"""

    provider_name = "minimax"
    service_type = ServiceType.embedding
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"embedding": "embo-01"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._http = get_http(config.base_url or "https://api.minimaxi.com", config.api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.config.model or "embo-01"
        payload = {"model": model, "texts": texts, "type": "db"}
        resp = await self._http.post("/v1/embeddings", json=payload)
        body = raise_for_minimax_response(resp, provider=self.provider_name, model=model)
        vectors = body.get("vectors") if isinstance(body, dict) else None
        if not vectors or not isinstance(vectors, list):
            raise ProviderError(
                "MiniMax embedding returned empty or invalid vectors",
                provider=self.provider_name,
                model=model,
                status_code=502,
            )
        return vectors
