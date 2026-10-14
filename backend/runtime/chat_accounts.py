"""账户持有已受理回合；连接租约只决定空闲运行态何时释放。"""

import asyncio
from dataclasses import dataclass, field

from infrastructure.logger import get_logger

from runtime.conversation import RuntimeSession
from runtime.task_supervisor import TASK_SUPERVISOR

logger = get_logger(__name__)


@dataclass
class AccountChatRuntime:
    user_id: int
    runtime_sessions: dict[str, RuntimeSession] = field(default_factory=dict)
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    connection_leases: set[str] = field(default_factory=set)

    def track(self, task: asyncio.Task) -> None:
        self.background_tasks.add(task)
        TASK_SUPERVISOR.register(
            task,
            owner="conversation.accounts",
            user_id=self.user_id,
            cancel_on_maintenance=False,
            on_error=lambda done: logger.warning(
                "account background task failed",
                extra={"user_id": self.user_id},
                exc_info=done.exception(),
            ),
        )

        def finished(done: asyncio.Task) -> None:
            self.background_tasks.discard(done)
            release_unused_account_runtime(self.user_id)

        task.add_done_callback(finished)


_ACCOUNTS: dict[int, AccountChatRuntime] = {}


def acquire_account_runtime(user_id: int, connection_id: str) -> AccountChatRuntime:
    account = _ACCOUNTS.setdefault(user_id, AccountChatRuntime(user_id))
    account.connection_leases.add(connection_id)
    return account


def release_account_runtime(user_id: int, connection_id: str) -> None:
    if (account := _ACCOUNTS.get(user_id)) is not None:
        account.connection_leases.discard(connection_id)
    release_unused_account_runtime(user_id)


def release_unused_account_runtime(user_id: int) -> None:
    account = _ACCOUNTS.get(user_id)
    if account is None or account.connection_leases or account.background_tasks:
        return
    if any(runtime.busy for runtime in account.runtime_sessions.values()):
        return
    _ACCOUNTS.pop(user_id, None)


async def _wait_turn_settlement(runtimes: list[RuntimeSession]) -> None:
    # 主任务的完成回调可能创建收尾任务；分两轮等待，不取消持久状态回写。
    for settling in (False, True):
        tasks = [runtime.settlement_task if settling else runtime.chat_task for runtime in runtimes]
        pending = [task for task in tasks if task is not None and task is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*(asyncio.shield(task) for task in pending), return_exceptions=True)


async def wait_user_turns(user_id: int) -> None:
    if (account := _ACCOUNTS.get(user_id)) is not None:
        await _wait_turn_settlement(list(account.runtime_sessions.values()))


async def drain_account_turns() -> None:
    accounts = tuple(_ACCOUNTS.values())
    await _wait_turn_settlement([runtime for account in accounts for runtime in account.runtime_sessions.values()])
    pending = [
        task
        for account in accounts
        for task in account.background_tasks
        if not task.done() and task is not asyncio.current_task()
    ]
    for task in pending:
        if not task.cancelling():
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
