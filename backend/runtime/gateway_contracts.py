"""业务回合使用的连接端口；WebSocket、身份校验和连接释放由入口维护。"""

import asyncio
from typing import Protocol

from runtime.chat_accounts import AccountChatRuntime
from runtime.client_context import ChatRequestClientContext
from runtime.conversation import RuntimeSession
from runtime.desktop.jsonrpc import Handler, JsonRpcDispatcher


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
    def __call__(self, method: str, handler: Handler) -> None: ...
