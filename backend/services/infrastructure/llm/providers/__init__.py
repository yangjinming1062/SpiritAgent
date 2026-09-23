from . import (
    gemini,  # noqa: F401 — 供应商子包须可被 bootstrap 以 providers.<pkg> 引用；注册由 bootstrap/registrations.py 执行
    grok,  # noqa: F401
    mimo,  # noqa: F401
    minimax,  # noqa: F401
    qwen,  # noqa: F401
)
from ._reference import resolve_reference_bytes
from ._size_aspect import SIZE_TO_ASPECT
from .base import (
    BaseProvider,
    ChatProvider,
    EmbeddingProvider,
    ImageAsset,
    ImageGenProvider,
    ImageGenRequest,
    ImageGenResult,
    ProviderConfig,
    ProviderError,
    ProviderResultUnknownError,
    ServiceType,
    STTProvider,
    STTResult,
    TTSProvider,
    TTSResult,
    VideoAsset,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
    VoiceDesignResult,
)
from .http import aclose_all, rotate_http_clients
from .mimo import (
    MiMoChatProvider,
    MiMoSTTProvider,
    MiMoTTSProvider,
)
from .registry import (
    OPENAI_COMPATIBLE_PROVIDERS,
    PROVIDER_DEFAULT_URLS,
    default_base_url,
    default_context_tokens_for,
    default_model_for,
    default_video_model_for,
    default_vision_model_for,
    providers_supporting,
    register,
    resolve,
    resolve_context_tokens,
    supports_video,
    supports_vision,
    try_resolve,
)

__all__ = [
    "OPENAI_COMPATIBLE_PROVIDERS",
    "PROVIDER_DEFAULT_URLS",
    "BaseProvider",
    "ChatProvider",
    "EmbeddingProvider",
    "ImageAsset",
    "ImageGenProvider",
    "ImageGenRequest",
    "ImageGenResult",
    "MiMoChatProvider",
    "MiMoSTTProvider",
    "MiMoTTSProvider",
    "ProviderConfig",
    "ProviderError",
    "ProviderResultUnknownError",
    "SIZE_TO_ASPECT",
    "STTProvider",
    "STTResult",
    "ServiceType",
    "TTSProvider",
    "TTSResult",
    "VideoAsset",
    "VideoGenProvider",
    "VideoGenRequest",
    "VideoJobStatus",
    "VoiceDesignResult",
    "aclose_all",
    "default_base_url",
    "default_context_tokens_for",
    "default_model_for",
    "default_video_model_for",
    "default_vision_model_for",
    "providers_supporting",
    "register",
    "resolve",
    "resolve_context_tokens",
    "resolve_reference_bytes",
    "rotate_http_clients",
    "supports_video",
    "supports_vision",
    "try_resolve",
]
