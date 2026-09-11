from ..base import ServiceType
from ..registry import register
from .chat import MiMoChatProvider
from .image import MiMoImageGenProvider
from .stt import MiMoSTTProvider
from .tts import MiMoTTSProvider

register(ServiceType.llm, "mimo", MiMoChatProvider)
register(ServiceType.stt, "mimo", MiMoSTTProvider)
register(ServiceType.tts, "mimo", MiMoTTSProvider)
register(ServiceType.image_gen, "mimo", MiMoImageGenProvider)

__all__ = ["MiMoChatProvider", "MiMoImageGenProvider", "MiMoSTTProvider", "MiMoTTSProvider"]
