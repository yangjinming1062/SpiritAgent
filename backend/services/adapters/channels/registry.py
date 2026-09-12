from components import get_logger

from .base import ChannelAdapter

logger = get_logger(__name__)

# channel_name → 适配器类；渠道族通过 import 本包并调用 register 自注册（镜像 services/llm/providers/registry.py）。
_REGISTRY: dict[str, type[ChannelAdapter]] = {}

# SpiritAgent 内置渠道；PUT /api/channels/{channel} 只接受其中之一，新增渠道需注册类并扩充下表。
KNOWN_CHANNELS: frozenset[str] = frozenset({"weixin_ilink"})


def register(name: str, cls: type[ChannelAdapter]) -> None:
    _REGISTRY[name] = cls


def resolve(name: str) -> type[ChannelAdapter]:
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise LookupError(f"No channel adapter registered for {name!r}") from e


def try_resolve(name: str) -> type[ChannelAdapter] | None:
    return _REGISTRY.get(name)


def registered_channels() -> list[str]:
    """按注册顺序排列的渠道名，供 GET /api/channels 与绑定校验使用。"""
    return list(_REGISTRY)


def channels_info() -> list[dict]:
    """渠道静态能力快照（REST 视图层）；未注册的 KNOWN 渠道跳过并告警，避免半启用状态。"""
    items = []
    for name in registered_channels():
        cls = _REGISTRY[name]
        items.append(
            {
                "channel": name,
                "title": cls.conversation_title,
                "capabilities": {
                    "supports_typing": cls.supports_typing,
                    "supports_media": cls.supports_media,
                    "can_initiate": cls.can_initiate,
                    "requires_login": cls.requires_login,
                },
            },
        )
    missing = KNOWN_CHANNELS - set(_REGISTRY)
    if missing:
        logger.warning("channels declared in KNOWN_CHANNELS but not registered: %s", sorted(missing))
    return items
