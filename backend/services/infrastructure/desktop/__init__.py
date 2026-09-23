"""桌面传输基础设施：连接管理器、JSON-RPC 编解码、IPC future 关联与重放缓冲。"""

from .buffer import (
    DEFAULT_REPLAY_BUFFER_CAPACITY,
    DEFAULT_REPLAY_BUFFER_TTL_SECONDS,
    ReplayBuffer,
)
from .connection import MANAGER, ConnectionManager
from .ipc import create_future, discard_call, discard_user, resolve_future, wait_future
from .jsonrpc import JsonRpcDispatcher, JsonRpcError

__all__ = [
    "DEFAULT_REPLAY_BUFFER_CAPACITY",
    "DEFAULT_REPLAY_BUFFER_TTL_SECONDS",
    "ConnectionManager",
    "JsonRpcDispatcher",
    "JsonRpcError",
    "MANAGER",
    "ReplayBuffer",
    "create_future",
    "discard_call",
    "discard_user",
    "resolve_future",
    "wait_future",
]
