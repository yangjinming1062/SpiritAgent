import asyncio
import contextlib
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from features.accounts.deps import is_remote_session_active
from features.accounts.gateway import GatewayIdentity
from features.accounts.session_operations import is_login_active
from features.companion.proactive_runtime import observe_companion_presence
from features.conversation.gateway_queries import ensure_main_conversation
from infrastructure.config import SETTINGS
from infrastructure.constants import JSONRPC_INVALID_PARAMS
from infrastructure.correlation import REQUEST_ID_HEADER, adopt_inbound
from infrastructure.logger import get_logger
from pydantic import ValidationError
from runtime.chat_accounts import (
    AccountChatRuntime,
    acquire_account_runtime,
    drain_account_turns,
    release_account_runtime,
    release_unused_account_runtime,
)
from runtime.client_context import ChatRequestClientContext
from runtime.conversation import RuntimeSession, ToolsSyncResult
from runtime.desktop.connection import MANAGER
from runtime.desktop.ipc import discard_user, resolve_future
from runtime.desktop.jsonrpc import JsonRpcDispatcher, JsonRpcError
from runtime.events.contracts import COMPANION_TURN_EVENT
from runtime.events.loop import interrupt_user_event_tasks
from runtime.task_supervisor import TASK_SUPERVISOR
from runtime.tools.registry import REGISTRY
from runtime.user_maintenance import is_user_in_maintenance
from workflows.chat.gateway import register_chat_handlers
from workflows.companion.gateway import forget_gateway_state
from workflows.companion.gateway import register_handlers as register_presence_handlers

from entrypoints.desktop.companion import register_handlers as register_companion_handlers
from entrypoints.desktop.contracts import RPC_CONTRACTS

logger = get_logger(__name__)


@dataclass
class UserGatewaySession:
    user_id: int
    login_record_id: int
    dispatcher: JsonRpcDispatcher
    session_client_context: ChatRequestClientContext | None
    account: AccountChatRuntime
    device_id: int | None = None
    connection_id: str = "desktop"
    websocket: WebSocket | None = None
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    grace_timer_task: asyncio.Task | None = None

    @property
    def runtime_sessions(self) -> dict[str, RuntimeSession]:
        return self.account.runtime_sessions

    @property
    def desktop_client_context(self) -> ChatRequestClientContext | None:
        desktop = _USER_SESSIONS.get(self.user_id)
        return desktop.session_client_context if desktop is not None else None

    @property
    def origin_kind(self) -> str:
        return "remote" if self.device_id is not None else "desktop"

    @property
    def origin_id(self) -> str:
        return str(self.device_id if self.device_id is not None else self.login_record_id)

    async def authorized(self) -> bool:
        if is_user_in_maintenance(self.user_id):
            return False
        if self.device_id is not None:
            return await is_remote_session_active(self.user_id, self.device_id)
        return await is_login_active(self.user_id, self.login_record_id)

    def track(self, task: asyncio.Task) -> None:
        self.background_tasks.add(task)
        TASK_SUPERVISOR.register(
            task,
            owner="channels.remote" if self.device_id is not None else "channels.desktop",
            user_id=self.user_id,
            on_error=lambda done: logger.warning(
                "gateway background task failed",
                extra={"user_id": self.user_id},
                exc_info=done.exception(),
            ),
        )

        def finished(done: asyncio.Task) -> None:
            self.background_tasks.discard(done)

        task.add_done_callback(finished)


_USER_SESSIONS: dict[int, UserGatewaySession] = {}
_REMOTE_SESSIONS: dict[int, dict[str, UserGatewaySession]] = {}
_GRACE_TIMER_TASKS: set[asyncio.Task] = set()
_USER_LOCKS: dict[int, asyncio.Lock] = {}
_MAX_IN_FLIGHT_REQUESTS = 32


def _user_lock(user_id: int) -> asyncio.Lock:
    return _USER_LOCKS.setdefault(user_id, asyncio.Lock())


async def _cancel_tasks(tasks: list[asyncio.Task | None]) -> None:
    pending = [task for task in tasks if task is not None and not task.done() and task is not asyncio.current_task()]
    for task in pending:
        if not task.cancelling():
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def drain() -> None:
    tasks: list[asyncio.Task | None] = list(_GRACE_TIMER_TASKS)
    for session in [
        *_USER_SESSIONS.values(),
        *(peer for peers in _REMOTE_SESSIONS.values() for peer in peers.values()),
    ]:
        tasks.extend(session.background_tasks)
    await _cancel_tasks(tasks)
    await drain_account_turns()
    for user_id in list(_REMOTE_SESSIONS):
        await terminate_remote_sessions(user_id)


async def _noop_send(data: dict[str, Any]) -> bool:
    return False


async def _terminate_user_gateway_locked(
    user_id: int,
    login_record_id: int | None = None,
) -> None:
    session = _USER_SESSIONS.get(user_id)
    if session is None or (login_record_id is not None and session.login_record_id != login_record_id):
        release_unused_account_runtime(user_id)
        return
    _USER_SESSIONS.pop(user_id, None)
    if (websocket := MANAGER.active_connections.get(user_id)) is not None:
        with contextlib.suppress(Exception):
            await websocket.close(code=1012 if is_user_in_maintenance(user_id) else 1008)
        MANAGER.disconnect(websocket, user_id)
    observe_companion_presence(user_id, False, presentation_mode=None)
    await _cancel_tasks([session.grace_timer_task, *session.background_tasks])
    await interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT)
    await MANAGER.aunregister_dispatcher(user_id)
    REGISTRY.clear_runner_tools(user_id)
    discard_user(user_id)
    forget_gateway_state(user_id)
    release_account_runtime(user_id, "desktop")


async def terminate_user_gateway(user_id: int, *, login_record_id: int | None = None) -> None:
    async with _user_lock(user_id):
        await _terminate_user_gateway_locked(user_id, login_record_id)


async def terminate_remote_sessions(user_id: int, device_id: int | None = None) -> None:
    async with _user_lock(user_id):
        for connection_id, session in list(_REMOTE_SESSIONS.get(user_id, {}).items()):
            if device_id is not None and session.device_id != device_id:
                continue
            _REMOTE_SESSIONS[user_id].pop(connection_id, None)
            if session.websocket is not None:
                with contextlib.suppress(Exception):
                    await session.websocket.close(code=1012 if is_user_in_maintenance(user_id) else 1008)
            await _cancel_tasks(list(session.background_tasks))
            await MANAGER.unregister_remote(user_id, connection_id)
            release_account_runtime(user_id, connection_id)
        if not _REMOTE_SESSIONS.get(user_id):
            _REMOTE_SESSIONS.pop(user_id, None)
        release_unused_account_runtime(user_id)


async def _expire_disconnected_gateway(user_id: int) -> None:
    try:
        await asyncio.sleep(SETTINGS.desktop_disconnect_grace_seconds)
        async with _user_lock(user_id):
            if not MANAGER.is_connected(user_id):
                await _terminate_user_gateway_locked(user_id)
    except asyncio.CancelledError:
        pass


def _ws_sender(websocket: WebSocket) -> Callable[[dict[str, Any]], Awaitable[bool]]:
    async def send(data: dict[str, Any]) -> bool:
        try:
            async with asyncio.timeout(15):
                await websocket.send_json(data)
            return True
        except WebSocketDisconnect:
            return False
        except RuntimeError as e:
            if "close message" not in str(e):
                logger.debug("websocket send RuntimeError", extra={"error": str(e)})
            return False
        except Exception as e:
            logger.debug("websocket send unexpected", extra={"error": str(e)}, exc_info=True)
            return False

    return send


async def _read_socket(websocket: WebSocket, session: UserGatewaySession) -> None:
    requests: set[asyncio.Task[None]] = set()
    try:
        while True:
            data = await websocket.receive_text()
            async with _user_lock(session.user_id):
                current = (
                    _REMOTE_SESSIONS.get(session.user_id, {}).get(session.connection_id) is session
                    if session.device_id is not None
                    else _USER_SESSIONS.get(session.user_id) is session
                    and MANAGER.active_connections.get(session.user_id) is websocket
                )
                if not current:
                    return
                valid = await session.authorized()
                if valid:
                    if len(requests) >= _MAX_IN_FLIGHT_REQUESTS:
                        await websocket.close(code=1013, reason="Too many pending requests")
                        return
                    dispatch_task = asyncio.create_task(session.dispatcher.handle_raw(data))
                    requests.add(dispatch_task)
                    dispatch_task.add_done_callback(requests.discard)
                    session.track(dispatch_task)
            if not valid:
                if session.device_id is not None:
                    await terminate_remote_sessions(session.user_id, session.device_id)
                else:
                    await terminate_user_gateway(session.user_id, login_record_id=session.login_record_id)
                return
    finally:
        # 请求归当前连接；已受理的聊天回合归账户，不随断连取消。
        await _cancel_tasks(list(requests))


async def handle_chat_websocket(websocket: WebSocket, ticket: GatewayIdentity) -> None:
    adopt_inbound(websocket.headers.get(REQUEST_ID_HEADER))
    if ticket.login_record_id is None:
        await websocket.close(code=1008)
        return
    user_id = ticket.user_id
    async with _user_lock(user_id):
        if is_user_in_maintenance(user_id) or not await is_login_active(user_id, ticket.login_record_id):
            await websocket.close(code=1012 if is_user_in_maintenance(user_id) else 1008)
            return
        session = _USER_SESSIONS.get(user_id)
        if session is not None and (session.login_record_id != ticket.login_record_id or MANAGER.is_connected(user_id)):
            await _terminate_user_gateway_locked(user_id)
            session = None
        try:
            await MANAGER.connect(websocket, user_id)
            await ensure_main_conversation(user_id)
            if session is None:
                account = acquire_account_runtime(user_id, "desktop")
                session = UserGatewaySession(
                    user_id=user_id,
                    login_record_id=ticket.login_record_id,
                    dispatcher=JsonRpcDispatcher(_ws_sender(websocket)),
                    session_client_context=ticket.client_context,
                    account=account,
                    websocket=websocket,
                )
                _USER_SESSIONS[user_id] = session
                _register_session_handlers(session)
            else:
                if session.grace_timer_task is not None:
                    session.grace_timer_task.cancel()
                    session.grace_timer_task = None
                session.dispatcher.set_sender(_ws_sender(websocket))
                session.session_client_context = ticket.client_context
                session.websocket = websocket
            session.dispatcher.enable_hold()
            MANAGER.register_dispatcher(user_id, session.dispatcher)
        except BaseException:
            logger.exception("desktop websocket initialization failed", extra={"user_id": user_id})
            await _terminate_user_gateway_locked(user_id)
            MANAGER.disconnect(websocket, user_id)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011)
            raise
    try:
        await _read_socket(websocket, session)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if MANAGER.active_connections.get(user_id) is websocket:
            MANAGER.disconnect(websocket, user_id)
            observe_companion_presence(user_id, False, presentation_mode=None)
            session.dispatcher.set_sender(_noop_send)
            task = asyncio.create_task(_expire_disconnected_gateway(user_id))
            _GRACE_TIMER_TASKS.add(task)
            TASK_SUPERVISOR.register(task, owner="channels.desktop.grace", user_id=user_id)
            task.add_done_callback(_GRACE_TIMER_TASKS.discard)
            session.grace_timer_task = task


async def handle_remote_websocket(websocket: WebSocket, identity: GatewayIdentity) -> None:
    adopt_inbound(websocket.headers.get(REQUEST_ID_HEADER))
    if identity.device_id is None:
        await websocket.close(code=1008)
        return
    user_id = identity.user_id
    session: UserGatewaySession | None = None
    connection_id: str | None = None
    async with _user_lock(user_id):
        if is_user_in_maintenance(user_id) or not await is_remote_session_active(user_id, identity.device_id):
            await websocket.close(code=1012 if is_user_in_maintenance(user_id) else 1008)
            return
        await websocket.accept()
        try:
            await ensure_main_conversation(user_id)
            connection_id = secrets.token_hex(16)
            account = acquire_account_runtime(user_id, connection_id)
            session = UserGatewaySession(
                user_id=user_id,
                login_record_id=0,
                device_id=identity.device_id,
                dispatcher=JsonRpcDispatcher(_ws_sender(websocket)),
                account=account,
                session_client_context=identity.client_context,
                connection_id=connection_id,
                websocket=websocket,
            )
            _REMOTE_SESSIONS.setdefault(user_id, {})[connection_id] = session
            _register_session_handlers(session)
            session.dispatcher.enable_hold()
            MANAGER.register_remote(user_id, connection_id, session.dispatcher)
        except BaseException:
            if session is not None:
                _REMOTE_SESSIONS.get(user_id, {}).pop(session.connection_id, None)
                await MANAGER.unregister_remote(user_id, session.connection_id)
            if connection_id is not None:
                release_account_runtime(user_id, connection_id)
            release_unused_account_runtime(user_id)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011)
            raise
    try:
        await _read_socket(websocket, session)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _REMOTE_SESSIONS.get(user_id, {}).pop(session.connection_id, None)
        if not _REMOTE_SESSIONS.get(user_id):
            _REMOTE_SESSIONS.pop(user_id, None)
        await MANAGER.unregister_remote(user_id, session.connection_id)
        await _cancel_tasks(list(session.background_tasks))
        release_account_runtime(user_id, session.connection_id)


def _register_session_handlers(session: UserGatewaySession) -> None:
    dispatcher = session.dispatcher
    replay_buffer = dispatcher.replay_buffer
    user_id = session.user_id

    def register(method: str, handler: Callable[[dict], Awaitable[Any]]) -> None:
        contract = RPC_CONTRACTS[method]
        if session.device_id is not None and not contract.remote:
            return

        async def validated(params: dict[str, Any]) -> Any:
            try:
                request = contract.request.model_validate(params)
            except ValidationError as exc:
                raise JsonRpcError(
                    JSONRPC_INVALID_PARAMS,
                    "Invalid request parameters",
                    details={
                        "validation": exc.errors(include_input=False, include_url=False, include_context=False),
                    },
                ) from exc
            return await handler(request.model_dump(exclude_unset=True))

        dispatcher.register(method, validated, concurrent=contract.concurrent)

    async def session_ack(params: dict) -> dict:
        seq = params["seq"]
        return {"acked": seq, "pruned": replay_buffer.ack(seq)}

    register("session.ack", session_ack)

    async def session_ping(_params: dict) -> dict:
        return {}

    register("session.ping", session_ping)

    async def tool_result_handler(params: dict) -> dict:
        call_id = params["call_id"]
        result = params["result"]
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        if not resolve_future(user_id, call_id, result):
            logger.warning("Future not found or already done", extra={"user_id": user_id, "call_id": call_id})
        return {}

    register("tool.result", tool_result_handler)

    async def tools_sync(params: dict) -> dict:
        tools = params["tools"]
        accepted: list[dict[str, Any]] = []
        for tool in tools:
            # 与服务端工具同名的本机工具永远派发不到（服务端优先），一并下发还会让模型看到重名工具。
            if REGISTRY.get_location(user_id, tool["name"]) in ("backend", "memory"):
                logger.warning(
                    "runner tool shadows a backend tool; skipped",
                    extra={"user_id": user_id, "tool_name": tool["name"]},
                )
                continue
            accepted.append(tool)
        REGISTRY.update_runner_tools(user_id, accepted)
        return ToolsSyncResult(count=len(accepted)).model_dump()

    register("tools.sync", tools_sync)

    register_chat_handlers(session, register)
    register_companion_handlers(session, register)
    register_presence_handlers(session, register)
