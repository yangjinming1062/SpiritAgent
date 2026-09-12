import contextlib
from collections.abc import Callable
from typing import Any

from components import get_logger
from fastapi import WebSocket

from .jsonrpc import JsonRpcDispatcher

logger = get_logger(__name__)

# 事件回路钩子由 event_store 在启动时装配（set_event_loop_hooks），
# 让传输层不反向依赖事件存储：新 dispatcher 注册时唤醒认领循环，writer 送达确认批量落库。
NotifyHook = Callable[[], None]
DeliverAckHook = Callable[[list[int]], Any]
_notify_hook: NotifyHook | None = None
_deliver_ack_hook: DeliverAckHook | None = None


def set_event_loop_hooks(*, notify: NotifyHook, deliver_ack: DeliverAckHook) -> None:
    global _notify_hook, _deliver_ack_hook
    _notify_hook = notify
    _deliver_ack_hook = deliver_ack


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, WebSocket] = {}
        self._login_record_ids: dict[int, int] = {}
        self._dispatchers: dict[int, JsonRpcDispatcher] = {}
        # 同会话 per-runtime map（同 _dispatchers 一并注册；存引用，runtime 挂载/卸载由 handlers.py 直接 mutate 同一 dict）。
        self._runtime_sessions: dict[int, dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket, user_id: int, login_record_id: int) -> None:
        """accept 并注册；同一用户已存在的 socket 也在此处关闭，把单设备登录不变量集中在一处，不与调用方分散。"""
        prior = self.active_connections.get(user_id)
        await websocket.accept()
        self.active_connections[user_id] = websocket
        self._login_record_ids[user_id] = login_record_id
        logger.info("User connected", extra={"user_id": user_id})
        if prior is not None:
            with contextlib.suppress(Exception):
                await prior.close(code=1000)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        if self.active_connections.get(user_id) is websocket:
            del self.active_connections[user_id]
            self._login_record_ids.pop(user_id, None)
            logger.info("User socket disconnected", extra={"user_id": user_id})

    def get_login_record_id(self, user_id: int) -> int | None:
        return self._login_record_ids.get(user_id)

    def register_dispatcher(self, user_id: int, dispatcher: JsonRpcDispatcher) -> None:
        self._dispatchers[user_id] = dispatcher
        dispatcher.start_writer()
        logger.info("User dispatcher registered", extra={"user_id": user_id})
        if _notify_hook is not None:
            _notify_hook()

    def register_runtime_sessions(self, user_id: int, runtime_sessions: dict[str, Any]) -> None:
        """注册同用户 runtime_sessions dict 引用——handlers.py 持续 mutate 此 dict，MANAGER 仅持有引用以供 REST 等非 WS 路径按 user_id 查表。"""
        self._runtime_sessions[user_id] = runtime_sessions

    def get_runtime_sessions(self, user_id: int) -> dict[str, Any] | None:
        return self._runtime_sessions.get(user_id)

    async def aunregister_dispatcher(self, user_id: int) -> None:
        dispatcher = self._dispatchers.pop(user_id, None)
        self._runtime_sessions.pop(user_id, None)
        if dispatcher is not None:
            await dispatcher.stop_writer()
            delivered = dispatcher.drain_delivered_ids()
            if delivered and _deliver_ack_hook is not None:
                with contextlib.suppress(Exception):
                    await _deliver_ack_hook(delivered)
        logger.info("User dispatcher async unregistered", extra={"user_id": user_id})

    def get_dispatcher(self, user_id: int) -> JsonRpcDispatcher | None:
        return self._dispatchers.get(user_id)

    def is_connected(self, user_id: int) -> bool:
        return user_id in self.active_connections

    def is_available(self, user_id: int) -> bool:
        return user_id in self.active_connections or user_id in self._dispatchers

    def local_user_ids(self) -> list[int]:
        """已注册 dispatcher 的 user_id 快照——outbox 轮询循环用它限定认领范围。"""
        return list(self._dispatchers.keys())


MANAGER = ConnectionManager()
