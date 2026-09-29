from .config import cfg_get, load_config

_config_passthrough: frozenset[str] | None = None


def _load_config_passthrough() -> frozenset[str]:
    global _config_passthrough
    if _config_passthrough is None:
        raw = cfg_get(load_config(), "terminal", "env_passthrough")
        items = raw if isinstance(raw, list) else []
        _config_passthrough = frozenset(name for item in items if isinstance(item, str) and (name := item.strip()))
    return _config_passthrough


def reset_cache() -> None:
    """清空由配置派生的透传变量集合（spiritagent.config.update 时调用）。"""
    global _config_passthrough
    _config_passthrough = None


def is_env_passthrough(var_name: str) -> bool:
    """``terminal.env_passthrough`` 列出的变量可穿过子进程环境的凭据过滤。"""
    return var_name in _load_config_passthrough()
