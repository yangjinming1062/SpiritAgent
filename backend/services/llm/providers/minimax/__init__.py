from ..base import ServiceType
from ..registry import register
from .chat import MiniMaxChatProvider
from .embedding import MiniMaxEmbeddingProvider
from .image import MiniMaxImageGenProvider
from .tts import MiniMaxTTSProvider
from .video import MiniMaxVideoGenProvider

register(ServiceType.llm, "minimax", MiniMaxChatProvider)
register(ServiceType.image_gen, "minimax", MiniMaxImageGenProvider)
register(ServiceType.video_gen, "minimax", MiniMaxVideoGenProvider)
register(ServiceType.tts, "minimax", MiniMaxTTSProvider)
register(ServiceType.embedding, "minimax", MiniMaxEmbeddingProvider)

__all__ = [
    "MiniMaxChatProvider",
    "MiniMaxEmbeddingProvider",
    "MiniMaxImageGenProvider",
    "MiniMaxTTSProvider",
    "MiniMaxVideoGenProvider",
]
