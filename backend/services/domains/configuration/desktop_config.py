from typing import Any

DEFAULT_CONFIG = {
    "agent": {"reasoning_effort": "low", "temperature": 0.7},
    "chat": {
        "enable_context_compression": True,
        "context_compression_threshold": 0.70,
        "title_generation_temperature": 0.3,
        "compression_temperature": 0.0,
    },
    "stt": {"enabled": True},
    "voice": {"max_recording_seconds": 60},
}


def settings_to_config(values: dict[str, Any]) -> dict[str, Any]:
    """点键设置展开为嵌套配置。"""
    config: dict[str, Any] = {}
    for key, value in values.items():
        *parents, leaf = key.split(".")
        node = config
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return config


def flatten_config(obj: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """嵌套配置压平为点键设置。"""
    items: dict[str, Any] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            items.update(flatten_config(v, f"{key}."))
        else:
            items[key] = v
    return items
