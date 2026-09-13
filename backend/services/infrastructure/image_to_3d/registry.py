from components import SETTINGS, get_logger

from .base import ImageTo3DError, ImageTo3DProvider

logger = get_logger(__name__)

_REGISTRY: dict[str, type[ImageTo3DProvider]] = {}


def register(provider_name: str, cls: type[ImageTo3DProvider]) -> None:
    _REGISTRY[provider_name] = cls


def resolve_provider(name: str | None = None) -> ImageTo3DProvider:
    provider_name = (name or SETTINGS.image_to_3d_provider or "tripo").strip().lower()

    if provider_name not in _REGISTRY:
        raise ImageTo3DError(f"未注册的图生3D供应商: {provider_name}")

    api_key = getattr(SETTINGS, f"{provider_name}_api_key", "") or ""
    if not api_key:
        raise ImageTo3DError(
            f"图生3D供应商 {provider_name} 未配置 API key（config.toml [image_to_3d] 段或 {provider_name.upper()}_API_KEY）",
        )

    return _REGISTRY[provider_name]()


def provider_supports_multiview(name: str | None = None) -> bool:
    """查询指定（或当前配置默认）图生3D供应商是否支持多视角模式。"""
    provider_name = (name or SETTINGS.image_to_3d_provider or "tripo").strip().lower()
    cls = _REGISTRY.get(provider_name)
    if cls is None:
        logger.warning("未知的图生3D供应商，多视角判定为 False", extra={"provider": provider_name})
        return False
    return bool(getattr(cls, "SUPPORTS_MULTIVIEW", False))
