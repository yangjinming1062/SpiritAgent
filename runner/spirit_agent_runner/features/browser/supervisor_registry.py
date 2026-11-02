import logging
import threading
from _thread import RLock
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from spirit_agent_runner.features.browser.dialog_manager import DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S
from spirit_agent_runner.features.browser.engine.launcher import NativeBrowserProcess
from spirit_agent_runner.features.browser.supervisor import CDPSupervisor

logger = logging.getLogger(__name__)

# 工具调用无 task 维度，浏览器会话/profile 共用固定键。
SESSION_KEY = "default"


@dataclass
class _LifecycleState:
    lock: RLock = field(default_factory=RLock)
    users: int = 0


class SupervisorRegistry:
    """单会话 CDP 主管的注册与串行启停锁。"""

    def __init__(self) -> None:
        self._supervisor: CDPSupervisor | None = None
        self._lifecycle = _LifecycleState()
        self._lock = threading.Lock()

    @contextmanager
    def lifecycle(self) -> Iterator[None]:
        """启动、替换和停止共用锁；等待者也计入 users，避免回收期间锁被重建。"""
        with self._lock:
            self._lifecycle.users += 1
        try:
            with self._lifecycle.lock:
                yield
        finally:
            with self._lock:
                self._lifecycle.users -= 1

    def get(self) -> CDPSupervisor | None:
        with self._lock:
            return self._supervisor

    def get_or_start(
        self,
        cdp_url: str,
        *,
        launch_handle: NativeBrowserProcess | None = None,
        auto_owned: bool = False,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
        timeout: float = 15.0,
    ) -> CDPSupervisor:
        with self.lifecycle():
            if (existing := self.get()) is not None and existing.active:
                return existing
            self.stop()
            sup = CDPSupervisor(
                cdp_url=cdp_url,
                launch_handle=launch_handle,
                auto_owned=auto_owned,
                dialog_policy=dialog_policy,
                dialog_timeout_s=dialog_timeout_s,
            )
            try:
                sup.start(timeout=timeout)
            except BaseException:
                try:
                    sup.stop()
                except Exception:
                    logger.exception("Error stopping failed supervisor")
                raise
            with self._lock:
                self._supervisor = sup
            return sup

    def stop(self, *, expected: CDPSupervisor | None = None) -> None:
        with self.lifecycle():
            with self._lock:
                if expected is not None and self._supervisor is not expected:
                    return
                sup = self._supervisor
                self._supervisor = None
            if sup is not None:
                sup.stop()
