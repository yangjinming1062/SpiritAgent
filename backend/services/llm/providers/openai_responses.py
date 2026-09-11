from typing import ClassVar

from openai import AsyncOpenAI

from .base import ChatProvider, ProviderConfig
from .http import get_async_client


class OpenAIResponsesChatProvider(ChatProvider):
    """Base provider for vendors exposing the OpenAI Responses protocol."""

    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset({"none", "low", "medium", "high"})
    SERVICE_TIERS: ClassVar[frozenset[str]] = frozenset()
    TEMPERATURE_MIN: ClassVar[float] = 0.0
    TEMPERATURE_MAX: ClassVar[float] = 2.0

    @classmethod
    def scale_temperature(cls, normalized: float) -> float:
        """把 [0, 1] 归一化温度映射到本供应商原生刻度：clamp(归一化值 × MAX, MIN, MAX)，保留两位小数。"""
        return round(max(cls.TEMPERATURE_MIN, min(cls.TEMPERATURE_MAX, normalized * cls.TEMPERATURE_MAX)), 2)

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client
