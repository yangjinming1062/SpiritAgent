import copy
import threading
from typing import Any

from spirit_agent_runner.contracts.tooling import ToolContext


class ConfigStore:
    """应用配置的唯一所有者；每次调用取得独立快照。"""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._generation = 0
        self._lock = threading.Lock()

    def update(self, config: dict[str, Any]) -> None:
        with self._lock:
            self._config = copy.deepcopy(config)
            self._generation += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._config)

    def context(self, **fields: Any) -> ToolContext:
        with self._lock:
            return ToolContext(config=copy.deepcopy(self._config), config_generation=self._generation, **fields)
