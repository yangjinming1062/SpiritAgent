from typing import ClassVar

from modules.memory import MEMORY_EMBEDDING_DIM

from ..base import EmbeddingProvider, ProviderConfig, ProviderError, ServiceType
from ..http import get_http


class GeminiEmbeddingProvider(EmbeddingProvider):
    """通过 Google Gemini 的 Generative Language API 提供 embeddings，支持 gemini-embedding-001 与 gemini-embedding-2。"""

    provider_name = "gemini"
    service_type = ServiceType.embedding
    DEFAULT_BASE_URL: ClassVar[str] = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL: ClassVar[str] = "gemini-embedding-001"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._http = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def embed(self, texts: list[str], *, purpose: str = "db") -> list[list[float]]:
        if not texts:
            return []
        model = self.config.model
        # 默认输出宽度与记忆库列宽不同，按列宽请求截断后的向量
        payload = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": t}]},
                    "outputDimensionality": MEMORY_EMBEDDING_DIM,
                }
                for t in texts
            ],
        }
        resp = await self._http.post(f"/v1beta/models/{model}:batchEmbedContents", json=payload)
        if resp.status_code != 200:
            raise ProviderError(
                f"Gemini embedding HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        embeddings_list = resp.json().get("embeddings", [])
        if not embeddings_list:
            raise ProviderError("Gemini embedding returned empty result")
        return [item.get("values", []) for item in embeddings_list]
