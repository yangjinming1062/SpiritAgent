"""账户持有已受理回合；连接租约只决定空闲运行态何时释放。"""

import asyncio
from dataclasses import dataclass, field

from infrastructure.async_io import cancel_and_wait, wait_for_task, wait_for_tasks
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError

from runtime.conversation import RuntimeSession
from runtime.task_supervisor import TASK_SUPERVISOR

logger = get_logger(__name__)


@dataclass
class AccountChatRuntime:
    user_id: int
    runtime_sessions: dict[str, RuntimeSession] = field(default_factory=dict)
    admission_tasks: dict[asyncio.Task, tuple[str, str]] = field(default_factory=dict)
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    connection_leases: set[str] = field(default_factory=set)

    def track(self, task: asyncio.Task) -> None:
        self.background_tasks.add(task)

        def report_error(done: asyncio.Task) -> None:
            error = done.exception()
            if isinstance(error, RequestError) and 400 <= error.status_code < 500:
                return
            logger.warning("account background task failed", extra={"user_id": self.user_id}, exc_info=error)

        TASK_SUPERVISOR.register(
            task,
            owner="conversation.accounts",
            user_id=self.user_id,
            cancel_on_maintenance=False,
            on_error=report_error,
        )

        def finished(done: asyncio.Task) -> None:
            self.background_tasks.discard(done)
            self.admission_tasks.pop(done, None)
            release_unused_account_runtime(self.user_id)

        task.add_done_callback(finished)

    def track_admission(self, task: asyncio.Task, session_id: str, request_id: str) -> None:
        self.admission_tasks[task] = (session_id, request_id)
        self.track(task)


_ACCOUNTS: dict[int, AccountChatRuntime] = {}


def get_account_runtime(user_id: int) -> AccountChatRuntime | None:
    return _ACCOUNTS.get(user_id)


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
    if account is None or account.connection_leases or account.background_tasks or account.admission_tasks:
        return
    if any(runtime.busy for runtime in account.runtime_sessions.values()):
        return
    _ACCOUNTS.pop(user_id, None)


async def _wait_turn_settlement(runtimes: list[RuntimeSession], owner: asyncio.Task | None) -> None:
    # 主任务的完成回调可能创建收尾任务；分两轮等待，不取消持久状态回写。
    for settling in (False, True):
        tasks = [runtime.settlement_task if settling else runtime.chat_task for runtime in runtimes]
        pending = [
            task for task in tasks if task is not None and task is not owner and task is not asyncio.current_task()
        ]
        if pending:
            await wait_for_tasks(pending)


async def _wait_admissions(user_id: int | None, owner: asyncio.Task | None) -> None:
    while pending := [
        task
        for account in tuple(_ACCOUNTS.values())
        if user_id is None or account.user_id == user_id
        for task in account.admission_tasks
        if not task.done() and task is not owner and task is not asyncio.current_task()
    ]:
        await wait_for_tasks(pending)


async def _wait_account_work(user_id: int | None = None) -> None:
    owner = asyncio.current_task()

    async def settle() -> None:
        await _wait_admissions(user_id, owner)
        accounts = tuple(account for account in _ACCOUNTS.values() if user_id is None or account.user_id == user_id)
        await _wait_turn_settlement(
            [runtime for account in accounts for runtime in account.runtime_sessions.values()],
            owner,
        )
        if user_id is None:
            await cancel_and_wait(
                task for account in accounts for task in account.background_tasks if task is not owner
            )

    await wait_for_task(asyncio.create_task(settle(), name="conversation.accounts.settle"))


async def wait_user_turns(user_id: int) -> None:
    await _wait_account_work(user_id)


async def drain_account_turns() -> None:
    await _wait_account_work()
