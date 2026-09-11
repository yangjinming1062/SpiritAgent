import json

from components import safe_json_loads
from modules.settings import UserSetting

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


def settings_to_config(settings: list[UserSetting]) -> dict:
    config: dict = {}
    for s in settings:
        parts = s.setting_key.split(".")
        node = config
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = safe_json_loads(s.setting_value, default=s.setting_value)
    return config


def flatten_config(obj: dict, prefix: str = "") -> list[tuple[str, str]]:
    items = []
    for k, v in obj.items():
        key = f"{prefix}{k}" if prefix else k
        if isinstance(v, dict):
            items.extend(flatten_config(v, f"{key}."))
        else:
            items.append((key, json.dumps(v) if v is not None else ""))
    return items
