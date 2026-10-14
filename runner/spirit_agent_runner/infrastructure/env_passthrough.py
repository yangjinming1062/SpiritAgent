from typing import Any

from spirit_agent_runner.contracts.config import cfg_get


def is_env_passthrough(var_name: str, *, config: dict[str, Any] | None = None) -> bool:
    raw = cfg_get((config or {}), "terminal", "env_passthrough")
    return var_name in {item.strip() for item in raw if isinstance(item, str)} if isinstance(raw, list) else False
