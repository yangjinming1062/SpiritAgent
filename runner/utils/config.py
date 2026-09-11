from typing import Any

_TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})

# 初始为 ``None``；Desktop 通过 spiritagent.config.update 推送前，消费者请走 cfg_get(default=...)。
_INMEMORY_CONFIG: dict[str, Any] | None = None


def is_truthy_value(value: Any, default: bool = False) -> bool:
    """把配置值归一为 bool：不在真值集合内的回落到 ``default``。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return bool(value)


def load_config() -> dict[str, Any]:
    """返回内存配置字典（Desktop 首次推送前为 ``{}``）。"""
    return _INMEMORY_CONFIG if _INMEMORY_CONFIG is not None else {}


def set_inmemory_config(config: dict[str, Any]) -> None:
    """覆盖内存配置；由 ``spiritagent.config.update`` RPC 处理函数调用。"""
    global _INMEMORY_CONFIG
    if not isinstance(config, dict):
        raise TypeError(f"config must be a dict, got {type(config).__name__}")
    _INMEMORY_CONFIG = config


def cfg_get(d: Any, *keys: str, default: Any = None) -> Any:
    """沿 ``d.get(k)`` 链逐层取值，任一缺失即返回 ``default``。"""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


def get_env_type(default: str = "local") -> str:
    """归一化 ``terminal.env_type``：去空白、转小写、缺失回落到 ``default``。"""
    val = cfg_get(load_config(), "terminal", "env_type", default=default)
    return str(val).strip().lower() or default


def cfg_str(section: dict[str, Any], key: str, default: str = "") -> str:
    """把配置值强制为字符串并去前后空白。"""
    v = section.get(key, default)
    return str(v).strip() if v is not None else default


def cfg_bool(section: dict[str, Any], key: str, default: bool = False) -> bool:
    """通过 ``is_truthy_value`` 把配置值转 bool。"""
    return is_truthy_value(section.get(key), default=default)


def cfg_int(section: dict[str, Any], key: str, default: int = 0) -> int:
    """把配置值转为 int；失败回落到 *default*。"""
    try:
        return int(section.get(key, default))
    except (TypeError, ValueError):
        return default


def get_disabled_config_names(section: str = "skills") -> set[str]:
    """读取 ``{section}.disabled`` 列表（适用于 ``skills``、``toolsets`` 等）。"""
    raw = cfg_get(load_config(), section, "disabled", default=[])
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if isinstance(item, str) and item.strip()}
