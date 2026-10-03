from modules.channels import ChannelInfo

from .base import ChannelAdapter

# 渠道键 → 适配器类；bootstrap/registrations.py 显式注册，未注册的渠道 PUT/启动均明确失败。
_REGISTRY: dict[str, type[ChannelAdapter]] = {}


def register(name: str, cls: type[ChannelAdapter]) -> None:
    _REGISTRY[name] = cls


def resolve(name: str) -> type[ChannelAdapter]:
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise LookupError(f"No channel adapter registered for {name!r}") from e


def try_resolve(name: str) -> type[ChannelAdapter] | None:
    return _REGISTRY.get(name)


def channels_info() -> list[ChannelInfo]:
    """按注册顺序列出渠道（不含绑定状态）。"""
    return [
        ChannelInfo(
            channel=name,
            title=cls.conversation_title,
        )
        for name, cls in _REGISTRY.items()
    ]
