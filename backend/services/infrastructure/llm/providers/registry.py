from components import SETTINGS, get_logger

from .base import BaseProvider, ChatProvider, ServiceType

logger = get_logger(__name__)

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


def default_model_for(provider: str, service_type: ServiceType) -> str:
    """返回供应商为该能力发布的默认模型名称；没有默认值时返回空字符串。"""
    cls = try_resolve(service_type, provider)
    return cls.DEFAULT_MODEL if cls is not None else ""


def provider_requires_api_key(service_type: ServiceType, provider_name: str) -> bool:
    cls = try_resolve(service_type, provider_name)
    return cls is None or cls.requires_api_key


def providers_supporting(service_type: ServiceType | str) -> list[str]:
    """按注册顺序排列、已注册该能力类的供应商名列表，供回退链筛选可尝试的供应商。"""
    svc = ServiceType(service_type)
    return [name for registered_svc, name in _REGISTRY if registered_svc == svc]


def resolve_context_tokens(provider: str) -> int:
    """chat 供应商声明的上下文窗口；未声明时回退到全局默认。"""
    cls = try_resolve_chat(provider)
    if cls is not None and cls.CONTEXT_TOKENS > 0:
        return cls.CONTEXT_TOKENS
    logger.warning(
        "resolve_context_tokens: no default published for provider=%r; falling through to global default %d",
        provider,
        SETTINGS.default_llm_context_tokens,
    )
    return SETTINGS.default_llm_context_tokens
