"""领域模型与协议 Schema 包；子包 `__init__` 对外 re-export。导入本包即把全部 ORM 模型注册到 ModelBase.metadata。"""

from . import auth, companion, conversation, media, memory, remote, scheduler, settings, system, update, ws

__all__ = [
    "auth",
    "companion",
    "conversation",
    "media",
    "memory",
    "remote",
    "scheduler",
    "settings",
    "system",
    "update",
    "ws",
]
