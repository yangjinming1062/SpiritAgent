"""领域模型与协议 Schema 包；子包 `__init__` 对外 re-export。导入本包即把全部 ORM 模型注册到 ModelBase.metadata。"""

from . import auth, channels, companion, conversation, media, memory, scheduler, settings, system, update, ws

__all__ = [
    "auth",
    "channels",
    "companion",
    "conversation",
    "media",
    "memory",
    "scheduler",
    "settings",
    "system",
    "update",
    "ws",
]
