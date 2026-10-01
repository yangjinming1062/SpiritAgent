"""桌面传输基础设施：连接管理器、JSON-RPC 编解码、设备指令等待与重放缓冲。"""

from .connection import MANAGER, set_event_loop_hooks
from .ipc import discard_user, dispatch_device_call, resolve_future
from .jsonrpc import JsonRpcDispatcher, JsonRpcError

__all__ = [
    "MANAGER",
    "JsonRpcDispatcher",
    "JsonRpcError",
    "discard_user",
    "dispatch_device_call",
    "resolve_future",
    "set_event_loop_hooks",
]
