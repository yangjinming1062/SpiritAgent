from typing import ClassVar

from ..base import ChatProvider


class MiMoChatProvider(ChatProvider):
    provider_name = "mimo"
    DEFAULT_BASE_URL: ClassVar[str] = "https://token-plan-cn.xiaomimimo.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "mimo-v2.6-pro"
    CONTEXT_TOKENS: ClassVar[int] = 1_000_000
    supports_vision: ClassVar[bool] = True
    DEFAULT_VISION_MODEL: ClassVar[str] = "mimo-v2.6-flash"
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset(
        {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
    )
    supports_json_object: ClassVar[bool] = True
    TEMPERATURE_MAX: ClassVar[float] = 1.5
