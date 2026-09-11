import logging
from collections.abc import Iterable
from contextvars import ContextVar

from .config import cfg_get, load_config

logger = logging.getLogger(__name__)
_allowed_env_vars_var: ContextVar[set[str]] = ContextVar("_allowed_env_vars")


def _get_allowed() -> set[str]:
    if (val := _allowed_env_vars_var.get(None)) is None:
        _allowed_env_vars_var.set(val := set())
    return val


_config_passthrough: frozenset[str] | None = None


def register_env_passthrough(var_names: Iterable[str]) -> None:
    for name in var_names:
        if not (name := name.strip()):
            continue
        _get_allowed().add(name)
        logger.debug("env passthrough: registered %s", name)


def _load_config_passthrough() -> frozenset[str]:
    global _config_passthrough
    if _config_passthrough is not None:
        return _config_passthrough
    result = set()
    try:
        if isinstance(passthrough := cfg_get(load_config(), "terminal", "env_passthrough"), list):
            for item in passthrough:
                if isinstance(item, str) and (name := item.strip()):
                    result.add(name)
    except Exception as e:
        logger.debug("Could not read tools.env_passthrough from config: %s", e)
    _config_passthrough = frozenset(result)
    return _config_passthrough


def reset_cache() -> None:
    """清空由配置派生的透传变量集合（spiritagent.config.update 时调用）。"""
    global _config_passthrough
    _config_passthrough = None


def is_env_passthrough(var_name: str) -> bool:
    return var_name in _get_allowed() or var_name in _load_config_passthrough()


def get_all_passthrough() -> frozenset[str]:
    return frozenset(_get_allowed()) | _load_config_passthrough()
