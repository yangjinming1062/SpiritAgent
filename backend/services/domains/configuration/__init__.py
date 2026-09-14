"""配置业务域：AI 供应商卡片与桌面默认配置的读写与校验。"""

from .ai_config import prepare_ai_config, public_ai_config
from .desktop_config import DEFAULT_CONFIG, flatten_config, settings_to_config

__all__ = [
    "DEFAULT_CONFIG",
    "flatten_config",
    "prepare_ai_config",
    "public_ai_config",
    "settings_to_config",
]
