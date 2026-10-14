"""账户操作使用的连接生命周期端口，由启动装配绑定具体传输实现。"""

from dataclasses import dataclass
from typing import Protocol


class DesktopCloser(Protocol):
    async def __call__(self, user_id: int, *, login_record_id: int | None = None) -> None: ...


class RemoteCloser(Protocol):
    async def __call__(self, user_id: int, device_id: int | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class ChannelLifecycle:
    close_desktop: DesktopCloser
    close_remote: RemoteCloser


_LIFECYCLE: ChannelLifecycle | None = None


def register_channel_lifecycle(lifecycle: ChannelLifecycle) -> None:
    global _LIFECYCLE
    _LIFECYCLE = lifecycle


def _channel_lifecycle() -> ChannelLifecycle:
    if _LIFECYCLE is None:
        raise RuntimeError("Channel lifecycle has not been registered")
    return _LIFECYCLE


async def terminate_user_gateway(user_id: int, *, login_record_id: int | None = None) -> None:
    await _channel_lifecycle().close_desktop(user_id, login_record_id=login_record_id)


async def terminate_remote_sessions(user_id: int, device_id: int | None = None) -> None:
    await _channel_lifecycle().close_remote(user_id, device_id)
