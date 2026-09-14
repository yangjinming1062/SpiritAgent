"""桌面 WS 适配层：连接鉴权、会话生命周期与 JSON-RPC 方法注册。"""

from .handlers import do_session_undo, drain, handle_chat_websocket, terminate_user_gateway

__all__ = ["do_session_undo", "drain", "handle_chat_websocket", "terminate_user_gateway"]
