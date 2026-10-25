from typing import ClassVar

from infrastructure.ai.providers._provider_errors import raise_for_provider_response
from infrastructure.ai.providers.base import EmbeddingProvider, ProviderConfig, ProviderError, ServiceType
from infrastructure.ai.providers.http import get_http


class GeminiEmbeddingProvider(EmbeddingProvider):
    """通过 Google Gemini 的 Generative Language API 提供 embeddings，支持 gemini-embedding-001 与 gemini-embedding-2。"""

    provider_name = "gemini"
    service_type = ServiceType.embedding
    DEFAULT_BASE_URL: ClassVar[str] = "https://generativelanguage.googleapis.com"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._http = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        if not texts:
            return []
        model = self.config.model
        payload = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": t}]},
                }
                for t in texts
            ],
        }
        resp = await self._http.post(f"/v1beta/models/{model}:batchEmbedContents", json=payload)
        body = raise_for_provider_response(resp, family="gemini")
        embeddings_list = body.get("embeddings", [])
        if not embeddings_list:
            raise ProviderError("Gemini embedding returned empty result", body=body)
        return [item.get("values", []) for item in embeddings_list]
