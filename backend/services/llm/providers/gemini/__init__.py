from ..base import ServiceType
from ..registry import register
from .embedding import GeminiEmbeddingProvider
from .image import GeminiImageGenProvider

register(ServiceType.image_gen, "gemini", GeminiImageGenProvider)
register(ServiceType.embedding, "gemini", GeminiEmbeddingProvider)

__all__ = ["GeminiEmbeddingProvider", "GeminiImageGenProvider"]
