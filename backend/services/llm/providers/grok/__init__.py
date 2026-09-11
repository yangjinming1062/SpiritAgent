from ..base import ServiceType
from ..registry import register
from .chat import GrokChatProvider
from .image import GrokImageGenProvider
from .stt import GrokSTTProvider
from .tts import GrokTTSProvider
from .video import GrokVideoGenProvider

register(ServiceType.llm, "grok", GrokChatProvider)
register(ServiceType.stt, "grok", GrokSTTProvider)
register(ServiceType.tts, "grok", GrokTTSProvider)
register(ServiceType.image_gen, "grok", GrokImageGenProvider)
register(ServiceType.video_gen, "grok", GrokVideoGenProvider)

__all__ = ["GrokChatProvider", "GrokImageGenProvider", "GrokSTTProvider", "GrokTTSProvider", "GrokVideoGenProvider"]
