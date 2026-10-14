"""执行环境、后台进程及空闲回收的唯一资源所有者。"""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from spirit_agent_runner.contracts.context import invocation_scope
from spirit_agent_runner.contracts.tooling import ToolContext
from spirit_agent_runner.features.execution.api import EnvironmentBusyError, execution_scope
from spirit_agent_runner.features.execution.environments._env_base import BaseEnvironment, EnvironmentSpec
from spirit_agent_runner.features.execution.environments.config import environment_spec, normalize_environment_config
from spirit_agent_runner.features.execution.environments.factory import create_environment
from spirit_agent_runner.features.execution.process.registry import ProcessRegistry

logger = logging.getLogger(__name__)

ToolInvoker = Callable[[ToolContext, str, dict[str, Any]], Any]

_TARGET_BUSY_MESSAGE = (
    "The terminal target was changed in settings while commands or background processes from the previous "
    "target are still running. Wait for them to finish before retrying; stopping them may interrupt the user's work, "
    "so ask the user before stopping any with the process tool."
)


class ExecutionManager:
    def __init__(self, get_config: Callable[[], dict[str, Any]], tool_invoker: ToolInvoker | None = None) -> None:
        self._get_config = get_config
        self._tool_invoker = tool_invoker
        self.processes = ProcessRegistry()
        self._environments: dict[str, BaseEnvironment] = {}
        self._last_activity: dict[str, float] = {}
        self._environment_lock = threading.Lock()
        self._creation_locks: dict[str, threading.Lock] = {}
        self._creation_locks_lock = threading.Lock()
        self._cleanup_environment: Callable[[str], None] | None = None
        self._stop_event = threading.Event()
        self._cleanup_thread: threading.Thread | None = None
        self._closed = False

    def set_tool_invoker(self, invoker: ToolInvoker) -> None:
        self._tool_invoker = invoker

    def set_environment_cleanup(self, cleanup: Callable[[str], None]) -> None:
        self._cleanup_environment = cleanup

    def invoke_tool(self, context: ToolContext, name: str, args: dict[str, Any]) -> Any:
        if self._tool_invoker is None:
            raise RuntimeError("Code execution tool invoker has not been assembled")
        return self._tool_invoker(context, name, args)

    def start(self) -> None:
        with self._environment_lock:
            if self._closed:
                raise RuntimeError("Execution manager has been closed")
            if self._cleanup_thread is not None:
                return
            self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True, name="execution-cleanup")
            self._cleanup_thread.start()

    def _lease(self, task_id: str, environment: BaseEnvironment) -> BaseEnvironment:
        environment.leases += 1
        self._last_activity[task_id] = time.monotonic()
        return environment

    def _acquire(self, task_id: str, spec: EnvironmentSpec) -> BaseEnvironment:
        with self._environment_lock:
            if self._closed:
                raise RuntimeError("Execution manager has been closed")
            if (environment := self._environments.get(task_id)) is not None and environment.spec == spec:
                return self._lease(task_id, environment)
        with self._creation_locks_lock:
            task_lock = self._creation_locks.setdefault(task_id, threading.Lock())
        with task_lock:
            with self._environment_lock:
                if self._closed:
                    raise RuntimeError("Execution manager has been closed")
                if (environment := self._environments.get(task_id)) is not None and environment.spec == spec:
                    return self._lease(task_id, environment)
                if environment is not None:
                    in_use = environment.in_use or self.processes.has_active_processes(task_id)
                    if in_use and environment.spec is not None and environment.spec.target == spec.target:
                        return self._lease(task_id, environment)
                    if in_use:
                        raise EnvironmentBusyError(_TARGET_BUSY_MESSAGE)
                    self._environments.pop(task_id, None)
                    self._last_activity.pop(task_id, None)
            if environment is not None:
                self._stop_environment(task_id, environment)
            environment = create_environment(spec)
            with self._environment_lock:
                if not self._closed:
                    self._environments[task_id] = environment
                    return self._lease(task_id, environment)
            self._stop_environment(task_id, environment)
            raise RuntimeError("Execution manager was closed while creating an environment")

    @contextmanager
    def use_environment(self, task_id: str, config: dict[str, Any]) -> Iterator[BaseEnvironment]:
        environment = self._acquire(task_id, environment_spec(config))
        try:
            yield environment
        finally:
            with self._environment_lock:
                environment.leases -= 1
                if self._environments.get(task_id) is environment:
                    self._last_activity[task_id] = time.monotonic()

    def _stop_environment(self, task_id: str, environment: BaseEnvironment) -> None:
        if self._cleanup_environment is not None:
            try:
                self._cleanup_environment(task_id)
            except Exception:
                logger.warning("File state cleanup failed for task %s", task_id, exc_info=True)
        try:
            environment.cleanup()
        except Exception:
            logger.warning("Environment cleanup failed for task %s", task_id, exc_info=True)

    def cleanup_inactive(self, lifetime_seconds: int) -> None:
        with self._environment_lock:
            task_ids = list(self._environments)
        for task_id in task_ids:
            with self._creation_locks_lock:
                task_lock = self._creation_locks.setdefault(task_id, threading.Lock())
            with task_lock:
                now = time.monotonic()
                with self._environment_lock:
                    environment = self._environments.get(task_id)
                    if environment is None:
                        continue
                    if environment.in_use or self.processes.has_active_processes(task_id):
                        self._last_activity[task_id] = now
                        continue
                    if now - self._last_activity.get(task_id, now) <= lifetime_seconds:
                        continue
                    self._environments.pop(task_id)
                    self._last_activity.pop(task_id, None)
                # 清理和同任务重建共用锁，旧环境不能清掉新环境的文件状态。
                self._stop_environment(task_id, environment)

    def _cleanup_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                config = self._get_config()
                with execution_scope(self), invocation_scope(ToolContext(config=config)):
                    self.cleanup_inactive(normalize_environment_config(config)["lifetime_seconds"])
            except Exception:
                logger.warning("Execution idle cleanup failed", exc_info=True)
            self._stop_event.wait(60)

    def close(self) -> None:
        with self._environment_lock:
            if self._closed:
                return
            self._closed = True
        self._stop_event.set()
        if self._cleanup_thread is not None:
            self._cleanup_thread.join()
        with execution_scope(self), invocation_scope(ToolContext(config=self._get_config())):
            self.processes.close()
            with self._environment_lock:
                environments = list(self._environments.items())
                self._environments.clear()
                self._last_activity.clear()
            for task_id, environment in environments:
                self._stop_environment(task_id, environment)
