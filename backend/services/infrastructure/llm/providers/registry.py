from collections.abc import Collection

from components import AI_FEATURE_BY_ID, SERVICE_OUTPUT_MODALITY, AiFeature, ModelModality, ProviderDefaults

from .base import (
    BaseProvider,
    ChatProvider,
    ImageGenProvider,
    ProviderConfig,
    ServiceType,
    TTSProvider,
    VideoGenProvider,
)

# (service_type, provider_name) → 具体类；由 bootstrap 显式注册。
_REGISTRY: dict[tuple[ServiceType, str], type[BaseProvider]] = {}


def register(
    service_type: ServiceType,
    provider_name: str,
    cls: type[BaseProvider],
) -> None:
    _REGISTRY[(service_type, provider_name)] = cls


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


def try_resolve_chat(provider_name: str) -> type[ChatProvider] | None:
    cls = _REGISTRY.get((ServiceType.llm, provider_name))
    return cls if cls is not None and issubclass(cls, ChatProvider) else None


def default_base_url(provider: str, service_type: ServiceType) -> str:
    cls = try_resolve(service_type, provider)
    return cls.DEFAULT_BASE_URL if cls is not None else ""


def configured_base_url(provider: str, base_url: str, service: ServiceType) -> str:
    value = (base_url or default_base_url(provider, service)).rstrip("/")
    if provider == "qwen":
        for suffix in ("/compatible-mode/v1", "/api/v1"):
            if value.endswith(suffix):
                target = (
                    "/api/v1"
                    if service in (ServiceType.tts, ServiceType.image_gen, ServiceType.video_gen)
                    else "/compatible-mode/v1"
                )
                return value.removesuffix(suffix) + target
    if provider == "minimax" and service != ServiceType.llm:
        value = value.removesuffix("/v1")
    return value


def provider_requires_api_key(service_type: ServiceType, provider_name: str) -> bool:
    cls = try_resolve(service_type, provider_name)
    return cls is None or cls.requires_api_key


def providers_supporting(service_type: ServiceType | str) -> list[str]:
    """按注册顺序排列、已注册该能力类的供应商名列表，供回退链筛选可尝试的供应商。"""
    svc = ServiceType(service_type)
    return [name for registered_svc, name in _REGISTRY if registered_svc == svc]


def provider_input_modalities(service: ServiceType | str, name: str) -> frozenset[ModelModality]:
    cls = try_resolve(ServiceType(service), name)
    if cls is None:
        return frozenset()
    if ServiceType(service) == ServiceType.stt:
        return frozenset({ModelModality.audio})
    supported = {ModelModality.text}
    if issubclass(cls, ChatProvider):
        if cls.supports_image:
            supported.add(ModelModality.image)
        if cls.supports_video:
            supported.add(ModelModality.video)
        if cls.supports_audio:
            supported.add(ModelModality.audio)
    elif (
        issubclass(cls, ImageGenProvider)
        and cls.supports_reference_image
        or issubclass(cls, VideoGenProvider)
        and (cls.supports_reference_images or cls.supports_first_frame or cls.supports_loop_frames)
    ):
        supported.add(ModelModality.image)
    return frozenset(supported)


def effective_input_modalities(config: ProviderConfig) -> frozenset[ModelModality]:
    return config.input_modalities & provider_input_modalities(config.service_type, config.provider_name)


def providers_for_feature(feature: AiFeature) -> list[str]:
    definition = AI_FEATURE_BY_ID[feature]
    service = ServiceType(definition.service)
    names = providers_supporting(service)
    names = [name for name in names if definition.required_input_modalities <= provider_input_modalities(service, name)]
    if feature == AiFeature.voice_design:
        names = [
            name
            for name in names
            if issubclass(provider := resolve(service, name), TTSProvider) and provider.VOICE_DESIGN_GUIDE is not None
        ]
    return names


def provider_configuration_defaults() -> dict[str, dict[str, ProviderDefaults]]:
    """端点与协议能力；不包含任何模型或 Token 预算默认值。"""
    result: dict[str, dict[str, ProviderDefaults]] = {}
    for (service, name), cls in _REGISTRY.items():
        result.setdefault(service.value, {})[name] = ProviderDefaults(
            base_url=cls.DEFAULT_BASE_URL,
            requires_api_key=cls.requires_api_key,
            voice_design_model=cls.VOICE_DESIGN_MODEL if issubclass(cls, TTSProvider) else None,
            input_modalities=provider_input_modalities(service, name),
        )
    return result


def model_supports_feature(
    provider_name: str,
    model_name: str,
    feature: AiFeature,
    *,
    input_modalities: Collection[ModelModality],
    output_modalities: Collection[ModelModality],
) -> bool:
    if provider_name not in providers_for_feature(feature):
        return False
    definition = AI_FEATURE_BY_ID[feature]
    if not definition.required_input_modalities <= set(input_modalities):
        return False
    if SERVICE_OUTPUT_MODALITY[definition.service] not in output_modalities:
        return False
    if feature == AiFeature.voice_design:
        cls = resolve(ServiceType.tts, provider_name)
        if issubclass(cls, TTSProvider) and cls.VOICE_DESIGN_MODEL is not None:
            return model_name == cls.VOICE_DESIGN_MODEL
    return True


def resolve_context_tokens(config: ProviderConfig) -> int:
    """调用窗口以能力卡片为准。"""
    if config.context_window is None:
        raise ValueError("LLM 能力卡片缺少上下文窗口配置")
    return config.context_window
