"""桌面传输基础设施：连接管理器、JSON-RPC 编解码、IPC future 关联与重放缓冲。"""

from services.infrastructure.desktop.buffer import (
    DEFAULT_REPLAY_BUFFER_CAPACITY,
    DEFAULT_REPLAY_BUFFER_TTL_SECONDS,
    BufferedFrame,
    ReplayBuffer,
)
from services.infrastructure.desktop.connection import MANAGER, ConnectionManager
from services.infrastructure.desktop.ipc import create_future, discard_call, discard_user, resolve_future, wait_future
from services.infrastructure.desktop.jsonrpc import Handler, JsonRpcDispatcher, JsonRpcError, redact_message

__all__ = [
    "DEFAULT_REPLAY_BUFFER_CAPACITY",
    "DEFAULT_REPLAY_BUFFER_TTL_SECONDS",
    "BufferedFrame",
    "ConnectionManager",
    "Handler",
    "JsonRpcDispatcher",
    "JsonRpcError",
    "MANAGER",
    "ReplayBuffer",
    "create_future",
    "discard_call",
    "discard_user",
    "redact_message",
    "resolve_future",
    "wait_future",
]
