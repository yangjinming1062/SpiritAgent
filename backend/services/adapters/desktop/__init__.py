"""桌面与手机 WS 适配层：独立连接身份、共享会话与受限 JSON-RPC。"""

from .handlers import (
    drain,
    handle_chat_websocket,
    handle_remote_websocket,
    terminate_remote_sessions,
    terminate_user_gateway,
    wait_user_turns,
)

__all__ = [
    "drain",
    "handle_chat_websocket",
    "handle_remote_websocket",
    "terminate_remote_sessions",
    "terminate_user_gateway",
    "wait_user_turns",
]
