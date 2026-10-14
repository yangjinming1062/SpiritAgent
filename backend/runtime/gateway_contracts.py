"""业务回合使用的连接端口；WebSocket、身份校验和连接释放由入口维护。"""

import asyncio
from typing import Any, Protocol, TypeGuard

from infrastructure.constants import JSONRPC_INVALID_PARAMS

from runtime.chat_accounts import AccountChatRuntime
from runtime.client_context import ChatRequestClientContext
from runtime.conversation import RuntimeSession
from runtime.desktop.jsonrpc import Handler, JsonRpcDispatcher, JsonRpcError


class GatewaySession(Protocol):
    user_id: int
    dispatcher: JsonRpcDispatcher
    session_client_context: ChatRequestClientContext | None
    account: AccountChatRuntime

    @property
    def runtime_sessions(self) -> dict[str, RuntimeSession]: ...

    @property
    def origin_kind(self) -> str: ...

    @property
    def origin_id(self) -> str: ...

    @property
    def desktop_client_context(self) -> ChatRequestClientContext | None: ...

    def track(self, task: asyncio.Task) -> None: ...


class RegisterMethod(Protocol):
    def __call__(self, method: str, handler: Handler, *, concurrent: bool = False) -> None: ...


def require_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return value


def optional_str(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is not None and not isinstance(value, str):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a string")
    return value


def is_nonneg_int(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def require_nonneg_int(params: dict[str, Any], key: str) -> int:
    value = params.get(key)
    if not is_nonneg_int(value):
        raise JsonRpcError(JSONRPC_INVALID_PARAMS, f"{key} must be a non-negative int")
    return value
