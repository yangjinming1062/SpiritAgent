"""桌面 WS 适配层：连接鉴权、会话生命周期与 JSON-RPC 方法注册。"""

from .handlers import drain, handle_chat_websocket, terminate_user_gateway

__all__ = ["drain", "handle_chat_websocket", "terminate_user_gateway"]
