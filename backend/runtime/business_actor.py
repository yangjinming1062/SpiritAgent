"""业务调用身份与账户运行态，不包含连接或传输对象。"""

from dataclasses import dataclass

from runtime.chat_accounts import AccountChatRuntime
from runtime.client_context import ChatRequestClientContext


@dataclass(frozen=True)
class BusinessActor:
    user_id: int
    origin_kind: str
    origin_id: str
    account: AccountChatRuntime
    client_context: ChatRequestClientContext | None = None
    execution_context: ChatRequestClientContext | None = None
