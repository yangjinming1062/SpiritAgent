"""执行能力的公开租约和调用入口，状态由装配的 ExecutionManager 持有。"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from spirit_agent_runner.contracts.context import current_context
from spirit_agent_runner.features.execution.environments._env_base import BaseEnvironment
from spirit_agent_runner.features.execution.environments.config import (
    normalize_environment_config,
    resolve_task_id,
)

if TYPE_CHECKING:
    from spirit_agent_runner.features.execution.manager import ExecutionManager
    from spirit_agent_runner.features.execution.process.registry import ProcessRegistry


class EnvironmentBusyError(RuntimeError):
    """执行目标变化时，旧目标上仍有调用或后台进程。"""


_current_manager: ContextVar["ExecutionManager"] = ContextVar("execution_manager")


@contextmanager
def execution_scope(manager: "ExecutionManager") -> Iterator[None]:
    token = _current_manager.set(manager)
    try:
        yield
    finally:
        _current_manager.reset(token)


def current_manager() -> "ExecutionManager":
    try:
        return _current_manager.get()
    except LookupError as exc:
        raise RuntimeError("Execution services have not been assembled for this call") from exc


def get_env_config() -> dict[str, Any]:
    return normalize_environment_config(current_context().config)


def current_task_id() -> str:
    return resolve_task_id(current_context().task_id)


@contextmanager
def use_environment(task_id: str) -> Iterator[BaseEnvironment]:
    with current_manager().use_environment(task_id, current_context().config) as environment:
        yield environment


def get_process_registry() -> "ProcessRegistry":
    return current_manager().processes


def invoke_tool(name: str, args: dict[str, Any]) -> Any:
    return current_manager().invoke_tool(current_context(), name, args)
