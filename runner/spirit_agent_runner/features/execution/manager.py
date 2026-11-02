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
    """单环境复用规则：无持有且无运行中后台进程才可重建；执行类型或 SSH 目标变化时拒绝新调用。"""

    def __init__(self, get_config: Callable[[], dict[str, Any]], tool_invoker: ToolInvoker | None = None) -> None:
        self._get_config = get_config
        self._tool_invoker = tool_invoker
        self.processes = ProcessRegistry()
        self._environment: BaseEnvironment | None = None
        self._last_activity = 0.0
        self._environment_lock = threading.Lock()
        self._creation_lock = threading.Lock()
        # 环境停止时清空 files 侧缓存；无参即清全部，单环境语义等价。
        self._cleanup_environment: Callable[[], None] | None = None
        self._stop_event = threading.Event()
        self._cleanup_thread: threading.Thread | None = None
        self._closed = False

    def set_tool_invoker(self, invoker: ToolInvoker) -> None:
        self._tool_invoker = invoker

    def set_environment_cleanup(self, cleanup: Callable[[], None]) -> None:
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

    def _lease(self, environment: BaseEnvironment) -> BaseEnvironment:
        environment.leases += 1
        self._last_activity = time.monotonic()
        return environment

    def _acquire(self, spec: EnvironmentSpec) -> BaseEnvironment:
        with self._creation_lock:
            with self._environment_lock:
                if self._closed:
                    raise RuntimeError("Execution manager has been closed")
                if (environment := self._environment) is not None and environment.spec == spec:
                    return self._lease(environment)
                if environment is not None and (environment.in_use or self.processes.has_active_processes()):
                    # 使用中仅 cwd／超时变化：沿用至空闲。
                    if environment.spec is not None and environment.spec.target == spec.target:
                        return self._lease(environment)
                    raise EnvironmentBusyError(_TARGET_BUSY_MESSAGE)
                self._environment = None
            if environment is not None:
                self._stop_environment(environment)
            environment = create_environment(spec)
            with self._environment_lock:
                if not self._closed:
                    self._environment = environment
                    return self._lease(environment)
            self._stop_environment(environment)
            raise RuntimeError("Execution manager was closed while creating an environment")

    @contextmanager
    def use_environment(self, config: dict[str, Any]) -> Iterator[BaseEnvironment]:
        environment = self._acquire(environment_spec(config))
        try:
            yield environment
        finally:
            with self._environment_lock:
                environment.leases -= 1
                if self._environment is environment:
                    self._last_activity = time.monotonic()

    def _stop_environment(self, environment: BaseEnvironment) -> None:
        if self._cleanup_environment is not None:
            try:
                self._cleanup_environment()
            except Exception:
                logger.warning("File state cleanup failed", exc_info=True)
        try:
            environment.cleanup()
        except Exception:
            logger.warning("Environment cleanup failed", exc_info=True)

    def cleanup_inactive(self, lifetime_seconds: int) -> None:
        with self._environment_lock:
            environment = self._environment
            if environment is None:
                return
            now = time.monotonic()
            if environment.in_use or self.processes.has_active_processes():
                self._last_activity = now
                return
            if now - self._last_activity <= lifetime_seconds:
                return
            self._environment = None
        self._stop_environment(environment)

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
                environment = self._environment
                self._environment = None
            if environment is not None:
                self._stop_environment(environment)
