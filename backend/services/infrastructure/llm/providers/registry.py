from components import SETTINGS, get_logger

from .base import BaseProvider, ServiceType

# (service_type, provider_name) → 具体类；供应商族通过 import 本模块并调用 register 完成自注册。
_REGISTRY: dict[tuple[ServiceType, str], type[BaseProvider]] = {}

# provider_name → service_type → 默认 MODEL_NAME；register() 时从各 provider 类的 DEFAULT_MODELS 镜像，default_model_for 查询；env 中 per-cap *_MODEL_NAME 覆盖优先。
_PROVIDER_DEFAULT_MODELS: dict[str, dict[str, str]] = {}

# provider_name → service_type → 默认 CONTEXT_TOKENS；register() 时从各 provider 类的 DEFAULT_CONTEXT_TOKENS 镜像；全局 SETTINGS.default_llm_context_tokens 兜底由 providers/__init__.py 的 resolve_context_tokens 包装应用，本表保持纯查找。
_PROVIDER_DEFAULT_CONTEXT_TOKENS: dict[str, dict[str, int]] = {}

# 接受图片输入的 chat 供应商集合；resolve_vision_provider 跳过其余。
_PROVIDER_SUPPORTS_VISION: set[str] = set()
# provider_name → 视觉 MODEL_NAME（空表示沿用文本默认）。
_PROVIDER_VISION_MODELS: dict[str, str] = {}
# 接受 Responses 形状 input_video 的 chat 供应商集合；resolve_video_chain 跳过其余。
_PROVIDER_SUPPORTS_VIDEO: set[str] = set()
# provider_name → 视频理解 MODEL_NAME（空表示沿用文本/视觉默认）。
_PROVIDER_VIDEO_MODELS: dict[str, str] = {}

# 提供 OpenAI 形态 /v1/embeddings 端点的供应商；原生端点（如 minimax /v1/embeddings 用 texts 而非 input）被排除，llm_client.resolve_embedding_provider 仅对集合内的供应商构造 OpenAI 兼容兜底。
OPENAI_COMPATIBLE_PROVIDERS: frozenset[str] = frozenset({"mimo"})

# (provider, service) 的默认 base_url；空字符串表示该供应商不提供该能力（如 MiniMax 无公开 STT）。MiMo 含 /v1（OpenAI SDK 需要完整 base_url），MiniMax 不含 /v1（其 httpx provider 自拼 /v1/<endpoint>）。
PROVIDER_DEFAULT_URLS: dict[str, dict[str, str]] = {
    "mimo": {
        "llm": "https://token-plan-cn.xiaomimimo.com/v1",
        "stt": "https://token-plan-cn.xiaomimimo.com/v1",
        "tts": "https://token-plan-cn.xiaomimimo.com/v1",
        "image_gen": "",
        "video_gen": "",
        "embedding": "",
    },
    "minimax": {
        "llm": "https://api.minimaxi.com/v1",
        "stt": "",
        "tts": "https://api.minimaxi.com",
        "image_gen": "https://api.minimaxi.com",
        "video_gen": "https://api.minimaxi.com",
        "embedding": "https://api.minimaxi.com",
    },
    "gemini": {
        "llm": "",
        "image_gen": "https://generativelanguage.googleapis.com",
        "embedding": "https://generativelanguage.googleapis.com",
    },
    "grok": {
        "llm": "https://api.x.ai/v1",
        "stt": "https://api.x.ai/v1",
        "tts": "https://api.x.ai/v1",
        "image_gen": "https://api.x.ai/v1",
        "video_gen": "https://api.x.ai/v1",
        "embedding": "",
    },
    "zhipu": {
        "llm": "",
        "stt": "https://open.bigmodel.cn/api/paas/v4",
        "tts": "https://open.bigmodel.cn/api/paas/v4",
        "image_gen": "https://open.bigmodel.cn/api/paas/v4",
        "video_gen": "",
        "embedding": "https://open.bigmodel.cn/api/paas/v4",
    },
}


def register(
    service_type: ServiceType,
    provider_name: str,
    cls: type[BaseProvider],
) -> None:
    _REGISTRY[(service_type, provider_name)] = cls
    # 把 DEFAULT_MODELS 镜像到 registry 缓存，能力解析无需 import 各 provider 类。
    for svc, model in getattr(cls, "DEFAULT_MODELS", {}).items():
        _PROVIDER_DEFAULT_MODELS.setdefault(provider_name, {})[svc] = model
    for svc, ctx in getattr(cls, "DEFAULT_CONTEXT_TOKENS", {}).items():
        _PROVIDER_DEFAULT_CONTEXT_TOKENS.setdefault(provider_name, {})[svc] = ctx
    # 镜像视觉能力与覆写，供 resolve_vision_provider 使用。
    if getattr(cls, "supports_vision", False):
        _PROVIDER_SUPPORTS_VISION.add(provider_name)
        vm = getattr(cls, "DEFAULT_VISION_MODELS", {}).get("llm", "")
        if vm:
            _PROVIDER_VISION_MODELS[provider_name] = vm
    # 镜像视频理解能力与覆写，供 resolve_video_chain 使用。
    if getattr(cls, "supports_video", False):
        _PROVIDER_SUPPORTS_VIDEO.add(provider_name)
        dm = getattr(cls, "DEFAULT_VIDEO_MODELS", {}).get("llm", "")
        if dm:
            _PROVIDER_VIDEO_MODELS[provider_name] = dm


def resolve(service_type: ServiceType, provider_name: str) -> type[BaseProvider]:
    try:
        return _REGISTRY[(service_type, provider_name)]
    except KeyError as e:
        raise LookupError(
            f"No provider registered for service={service_type.value!r}, provider={provider_name!r}",
        ) from e


def try_resolve(
    service_type: ServiceType,
    provider_name: str,
) -> type[BaseProvider] | None:
    return _REGISTRY.get((service_type, provider_name))


def default_base_url(provider: str, service_type: str) -> str:
    return PROVIDER_DEFAULT_URLS.get(provider, {}).get(service_type, "")


def default_model_for(provider: str, service_type: str) -> str:
    """返回供应商为该能力发布的默认模型名称；没有默认值时返回空字符串。"""
    return _PROVIDER_DEFAULT_MODELS.get(provider, {}).get(service_type, "")


def default_context_tokens_for(provider: str, service_type: str) -> int:
    # 0 表示"未发布默认值"，由解析器回退到终端兜底；0 的具体含义由调用方决定。
    return _PROVIDER_DEFAULT_CONTEXT_TOKENS.get(provider, {}).get(service_type, 0)


def supports_vision(provider_name: str) -> bool:
    """是否注册了具备视觉能力的 chat 类。"""
    return provider_name in _PROVIDER_SUPPORTS_VISION


def default_vision_model_for(provider_name: str) -> str:
    """视觉 MODEL_NAME；空字符串表示沿用普通 llm 模型（视觉与文本共用一个）。"""
    return _PROVIDER_VISION_MODELS.get(provider_name, "")


def supports_video(provider_name: str) -> bool:
    """是否注册了接受 Responses 形状 input_video 的 chat 类。"""
    return provider_name in _PROVIDER_SUPPORTS_VIDEO


def default_video_model_for(provider_name: str) -> str:
    """视频理解 MODEL_NAME；空字符串表示沿用普通 llm 模型（视频与文本共用一个）。"""
    return _PROVIDER_VIDEO_MODELS.get(provider_name, "")


def providers_supporting(service_type: ServiceType | str) -> list[str]:
    """按注册顺序排列、已注册该能力类的供应商名列表，供回退链筛选可尝试的供应商。"""
    svc = ServiceType(service_type) if not isinstance(service_type, ServiceType) else service_type
    return list(
        dict.fromkeys(name for registered_svc, name in _REGISTRY if registered_svc == svc),
    )


def resolve_context_tokens(provider: str, service_type: ServiceType | str) -> int:
    svc = service_type.value if isinstance(service_type, ServiceType) else service_type
    per_provider = default_context_tokens_for(provider, svc)
    if per_provider > 0:
        return per_provider
    get_logger(__name__).warning(
        "resolve_context_tokens: no default published for (provider=%r, service=%r); falling through to global default %d",
        provider,
        service_type,
        SETTINGS.default_llm_context_tokens,
    )
    return SETTINGS.default_llm_context_tokens
