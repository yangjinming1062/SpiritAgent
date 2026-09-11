from typing import ClassVar

import httpx
from openai import AsyncOpenAI

from .base import EmbeddingProvider, ProviderConfig, ProviderError
from .http import get_async_client


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """任意 OpenAI 兼容 /embeddings 端点供应商共用基类。"""

    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"embedding": "text-embedding-3-small"}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.config.model or "text-embedding-3-small"
        try:
            res = await self._client.embeddings.create(input=texts, model=model)
            return [item.embedding for item in sorted(res.data, key=lambda x: x.index)]
        except Exception as exc:
            # 保留 status_code + 结构化 body —— 让 error_classifier 的 Phase B（错误码 fallback）可读；
            # 之前直接重抛 ProviderError(...) 会丢掉这些字段，强制走消息模式匹配（脆弱）。
            body = getattr(exc, "body", None)
            if body is None:
                response = getattr(exc, "response", None)
                if isinstance(response, httpx.Response):
                    try:
                        json_body = response.json()
                        body = json_body if isinstance(json_body, dict) else None
                    except Exception:
                        body = None
            raise ProviderError(
                f"{self.provider_name} embedding error: {exc}",
                status_code=getattr(exc, "status_code", None),
                body=body,
                provider=self.provider_name,
                model=model,
            ) from exc
