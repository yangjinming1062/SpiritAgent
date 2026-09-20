from components import SETTINGS, get_logger

from .base import CharacterModelError, CharacterModelProvider

logger = get_logger(__name__)

_REGISTRY: dict[str, type[CharacterModelProvider]] = {}


def register(provider_name: str, cls: type[CharacterModelProvider]) -> None:
    _REGISTRY[provider_name] = cls


def resolve_provider(name: str | None = None) -> CharacterModelProvider:
    provider_name = (name or SETTINGS.model_generation_provider or "tripo").strip().lower()

    if provider_name not in _REGISTRY:
        raise CharacterModelError(f"未注册的模型生成供应商: {provider_name}")

    api_key = getattr(SETTINGS, f"{provider_name}_api_key", "") or ""
    if not api_key:
        raise CharacterModelError(
            f"模型生成供应商 {provider_name} 未配置 API key（config.toml [model_generation] 段或 {provider_name.upper()}_API_KEY）",
        )

    return _REGISTRY[provider_name]()


def provider_supports_multiview(name: str | None = None) -> bool:
    """查询指定（或当前配置默认）模型生成供应商是否支持多视角模式。"""
    provider_name = (name or SETTINGS.model_generation_provider or "tripo").strip().lower()
    cls = _REGISTRY.get(provider_name)
    if cls is None:
        logger.warning("未知的模型生成供应商，多视角判定为 False", extra={"provider": provider_name})
        return False
    return bool(getattr(cls, "SUPPORTS_MULTIVIEW", False))
