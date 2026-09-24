from typing import ClassVar

from ..base import ServiceType
from ..openai_responses import OpenAIResponsesChatProvider


class MiMoChatProvider(OpenAIResponsesChatProvider):
    provider_name = "mimo"
    service_type = ServiceType.llm
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.6-pro"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"llm": 1_000_000}
    supports_vision: ClassVar[bool] = True
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {"llm": "mimo-v2.6-flash"}
    REASONING_EFFORTS: ClassVar[frozenset[str]] = frozenset(
        {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
    )
    supports_json_object: ClassVar[bool] = True
    TEMPERATURE_MAX: ClassVar[float] = 1.5
