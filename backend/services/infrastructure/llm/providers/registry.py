from components import AI_FEATURE_BY_ID, AiFeature, ProviderDefaults

from .base import BaseProvider, ChatProvider, ProviderConfig, ServiceType, TTSProvider

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


def provider_requires_api_key(service_type: ServiceType, provider_name: str) -> bool:
    cls = try_resolve(service_type, provider_name)
    return cls is None or cls.requires_api_key


def providers_supporting(service_type: ServiceType | str) -> list[str]:
    """按注册顺序排列、已注册该能力类的供应商名列表，供回退链筛选可尝试的供应商。"""
    svc = ServiceType(service_type)
    return [name for registered_svc, name in _REGISTRY if registered_svc == svc]


def providers_for_feature(feature: AiFeature) -> list[str]:
    definition = AI_FEATURE_BY_ID[feature]
    service = ServiceType(definition.service)
    names = providers_supporting(service)
    if definition.vision:
        names = [
            name for name in names if (provider := try_resolve_chat(name)) is not None and provider.supports_vision
        ]
    if feature == AiFeature.voice_design:
        names = [
            name
            for name in names
            if issubclass(provider := resolve(service, name), TTSProvider) and provider.VOICE_DESIGN_GUIDE is not None
        ]
    return names


def provider_configuration_defaults() -> dict[str, dict[str, ProviderDefaults]]:
    """管理页预填模型与窗口，并用适配器声明核对供应商配置状态。"""
    result: dict[str, dict[str, ProviderDefaults]] = {}
    for (service, name), cls in _REGISTRY.items():
        result.setdefault(service.value, {})[name] = ProviderDefaults(
            model_name=cls.DEFAULT_MODEL,
            base_url=cls.DEFAULT_BASE_URL,
            requires_api_key=cls.requires_api_key,
            context_window=cls.DEFAULT_CONTEXT_TOKENS if issubclass(cls, ChatProvider) else None,
            voice_design_model=cls.VOICE_DESIGN_MODEL if issubclass(cls, TTSProvider) else None,
        )
    return result


def model_supports_feature(provider_name: str, model_name: str, feature: AiFeature) -> bool:
    if provider_name not in providers_for_feature(feature):
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
