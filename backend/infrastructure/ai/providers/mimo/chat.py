from typing import ClassVar

from infrastructure.ai.providers.base import ChatProvider


class MiMoChatProvider(ChatProvider):
    supports_audio: ClassVar[bool] = True
    supports_video: ClassVar[bool] = True
    provider_name = "mimo"
    DEFAULT_BASE_URL: ClassVar[str] = "https://token-plan-cn.xiaomimimo.com/v1"
    supports_image: ClassVar[bool] = True
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset(
        {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
    )
    supports_json_object: ClassVar[bool] = True
    TEMPERATURE_MAX: ClassVar[float] = 1.5
