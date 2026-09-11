from typing import ClassVar

from ..base import EmbeddingProvider, ProviderConfig, ProviderError, ServiceType
from ..http import get_http


class GeminiEmbeddingProvider(EmbeddingProvider):
    """通过 Google Gemini 的 Generative Language API 提供 embeddings，支持 gemini-embedding-001 与 gemini-embedding-2。"""

    provider_name = "gemini"
    service_type = ServiceType.embedding
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"embedding": "gemini-embedding-001"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._http = get_http(
            config.base_url or "https://generativelanguage.googleapis.com",
            config.api_key,
            auth_header={"x-goog-api-key": "{api_key}"},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.config.model or "gemini-embedding-001"
        payload = {"requests": [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}} for t in texts]}
        resp = await self._http.post(f"/v1beta/models/{model}:batchEmbedContents", json=payload)
        if resp.status_code != 200:
            raise ProviderError(
                f"Gemini embedding HTTP {resp.status_code}: {resp.text[:200]}",
                provider=self.provider_name,
                model=model,
                status_code=resp.status_code,
            )
        data = resp.json()
        embeddings_list = data.get("embeddings", [])
        if not embeddings_list:
            raise ProviderError("Gemini embedding returned empty result", provider=self.provider_name, model=model)
        return [item.get("values", []) for item in embeddings_list]
