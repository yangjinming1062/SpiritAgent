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


@dataclass
class _LifecycleState:
    lock: RLock = field(default_factory=RLock)
    users: int = 0


class SupervisorRegistry:
    def __init__(self) -> None:
        self._supervisors: dict[str, CDPSupervisor] = {}
        self._lifecycles: dict[str, _LifecycleState] = {}
        self._lock = threading.Lock()

    @contextmanager
    def lifecycle(self, task_id: str) -> Iterator[None]:
        """同 task 的启动、替换和停止共用锁；等待者也持有锁条目，避免回收后分裂成两把锁。"""
        with self._lock:
            if (state := self._lifecycles.get(task_id)) is None:
                state = _LifecycleState()
                self._lifecycles[task_id] = state
            state.users += 1
        try:
            with state.lock:
                yield
        finally:
            with self._lock:
                state.users -= 1
                if state.users == 0 and task_id not in self._supervisors:
                    self._lifecycles.pop(task_id, None)

    def get(self, task_id: str) -> CDPSupervisor | None:
        with self._lock:
            return self._supervisors.get(task_id)

    def get_or_start(
        self,
        task_id: str,
        cdp_url: str,
        *,
        launch_handle: NativeBrowserProcess | None = None,
        auto_owned: bool = False,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
        timeout: float = 15.0,
    ) -> CDPSupervisor:
        with self.lifecycle(task_id):
            existing = self.get(task_id)
            if existing is not None and existing.active:
                return existing
            self.stop(task_id)
            sup = CDPSupervisor(
                task_id=task_id,
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
                    logger.exception("Error stopping failed supervisor %s", task_id)
                raise
            with self._lock:
                self._supervisors[task_id] = sup
            return sup

    def stop(self, task_id: str, *, expected: CDPSupervisor | None = None) -> None:
        with self.lifecycle(task_id):
            with self._lock:
                sup = self._supervisors.get(task_id)
                if expected is not None and sup is not expected:
                    return
                self._supervisors.pop(task_id, None)
            if sup is not None:
                sup.stop()
