from typing import ClassVar

from modules.memory import MEMORY_EMBEDDING_DIM

from ..base import ServiceType
from ..openai_compat import OpenAIEmbeddingProvider


class QwenEmbeddingProvider(OpenAIEmbeddingProvider):
    """通过千问 OpenAI 兼容 /embeddings 提供 embeddings；默认 text-embedding-v4 原生 1024 维，按记忆库列宽请求输出。"""

    provider_name = "qwen"
    service_type = ServiceType.embedding
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    DEFAULT_MODEL: ClassVar[str] = "text-embedding-v4"

    def _request_dimensions(self) -> int | None:
        # 仅默认模型确认支持该宽度；其他模型按原生宽度返回，与列宽不符时由记忆层降级为关键词召回。
        return MEMORY_EMBEDDING_DIM if self.config.model == self.DEFAULT_MODEL else None
