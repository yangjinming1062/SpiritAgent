import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from components import get_logger
from fastapi import WebSocket

from .jsonrpc import JsonRpcDispatcher

logger = get_logger(__name__)

REMOTE_EVENT_PREFIXES = (
    "message.",
    "tool.start",
    "tool.complete",
    "session.state",
    "session.list_changed",
    "command.result",
    "compress.completed",
    "error",
    "system.notification",
    "video_gen.",
    "companion.message",
    "companion.mood",
    "companion.post",
    "companion.diary",
    "companion.scene",
    "companion.outfit",
    "companion.video",
    "companion.character_card",
    "avatar.regenerated",
    "memory.changed",
)


def remote_event_allowed(event_type: str) -> bool:
    return event_type.startswith(REMOTE_EVENT_PREFIXES)


# 事件回路钩子由 event_store 启动时装配（set_event_loop_hooks），传输层不反向依赖事件存储：注册唤醒认领，writer 送达确认批量落库。
NotifyHook = Callable[[], None]
DeliverAckHook = Callable[[list[int]], Awaitable[None]]
_notify_hook: NotifyHook | None = None
_deliver_ack_hook: DeliverAckHook | None = None


def set_event_loop_hooks(*, notify: NotifyHook, deliver_ack: DeliverAckHook) -> None:
    global _notify_hook, _deliver_ack_hook
    _notify_hook = notify
    _deliver_ack_hook = deliver_ack


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[int, WebSocket] = {}
        self._dispatchers: dict[int, JsonRpcDispatcher] = {}
        self._remote_dispatchers: dict[int, dict[str, JsonRpcDispatcher]] = {}

    async def connect(self, websocket: WebSocket, user_id: int) -> None:
        """accept 并注册；同一用户已存在的 socket 也在此处关闭，把单设备登录不变量集中在一处，不与调用方分散。"""
        prior = self.active_connections.get(user_id)
        await websocket.accept()
        self.active_connections[user_id] = websocket
        logger.info("User connected", extra={"user_id": user_id})
        if prior is not None:
            with contextlib.suppress(Exception):
                await prior.close(code=1000)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        if self.active_connections.get(user_id) is websocket:
            del self.active_connections[user_id]
            logger.info("User socket disconnected", extra={"user_id": user_id})

    def register_dispatcher(self, user_id: int, dispatcher: JsonRpcDispatcher) -> None:
        self._dispatchers[user_id] = dispatcher
        dispatcher.start_writer()
        logger.info("User dispatcher registered", extra={"user_id": user_id})
        if _notify_hook is not None:
            _notify_hook()

    async def aunregister_dispatcher(self, user_id: int) -> None:
        dispatcher = self._dispatchers.pop(user_id, None)
        if dispatcher is not None:
            await self._stop_dispatcher(user_id, dispatcher)
        logger.info("User dispatcher async unregistered", extra={"user_id": user_id})

    async def _stop_dispatcher(self, user_id: int, dispatcher: JsonRpcDispatcher) -> None:
        await dispatcher.stop_writer()
        delivered = dispatcher.drain_delivered_ids()
        if delivered and _deliver_ack_hook is not None:
            try:
                await _deliver_ack_hook(delivered)
            except Exception:
                # 确认失败由 outbox 过期锁恢复，不阻断连接资源释放。
                logger.warning("mark delivered outbox events failed", extra={"user_id": user_id}, exc_info=True)

    def get_dispatcher(self, user_id: int) -> JsonRpcDispatcher | None:
        return self._dispatchers.get(user_id)

    def is_connected(self, user_id: int) -> bool:
        return user_id in self.active_connections

    def is_available(self, user_id: int) -> bool:
        return user_id in self.active_connections or user_id in self._dispatchers

    def local_user_ids(self) -> list[int]:
        """已注册 dispatcher 的 user_id 快照——outbox 轮询循环用它限定认领范围。"""
        return list(self._dispatchers.keys())

    def register_remote(self, user_id: int, connection_id: str, dispatcher: JsonRpcDispatcher) -> None:
        self._remote_dispatchers.setdefault(user_id, {})[connection_id] = dispatcher
        dispatcher.start_writer()
        if _notify_hook is not None:
            _notify_hook()

    async def unregister_remote(self, user_id: int, connection_id: str) -> None:
        peers = self._remote_dispatchers.get(user_id, {})
        dispatcher = peers.pop(connection_id, None)
        if not peers:
            self._remote_dispatchers.pop(user_id, None)
        if dispatcher is not None:
            await self._stop_dispatcher(user_id, dispatcher)

    def event_user_ids(self) -> list[int]:
        return list(self._dispatchers.keys() | self._remote_dispatchers.keys())

    def event_dispatchers(self, user_id: int, event_type: str | None = None) -> list[JsonRpcDispatcher]:
        peers = []
        if (desktop := self.get_dispatcher(user_id)) is not None:
            peers.append(desktop)
        if event_type is None or remote_event_allowed(event_type):
            peers.extend(self._remote_dispatchers.get(user_id, {}).values())
        return peers

    async def publish_event(
        self,
        user_id: int,
        event_type: str,
        payload: object = None,
        *,
        session_id: str | None = None,
        event_id: int | None = None,
    ) -> bool:
        peers = self.event_dispatchers(user_id, event_type)
        if not peers:
            return False
        results = await asyncio.gather(
            *(peer.enqueue_event(event_type, payload, session_id=session_id, event_id=event_id) for peer in peers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(
                    "shared event enqueue failed",
                    extra={"user_id": user_id, "event_type": event_type},
                    exc_info=result,
                )
        return any(result is True for result in results)


class UserEventPublisher:
    """业务事件扇出与连接级 RPC/ACK 分离，设备指令仍只走桌面 dispatcher。"""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    async def push_event(self, event_type: str, payload: object = None, session_id: str | None = None) -> None:
        await MANAGER.publish_event(self.user_id, event_type, payload, session_id=session_id)


MANAGER = ConnectionManager()
