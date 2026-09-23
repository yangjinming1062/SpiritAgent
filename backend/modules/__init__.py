"""领域模型与协议 Schema 包：按数据库/契约拥有行结构与载荷定义；子包 `__init__` 对外 re-export。

导入每个子包会注册其 ORM 模型到 ModelBase.metadata。
modules.media 故意省略——其 mapper 在陈旧 DB 连接上会触发 import-time crash，
需要 VideoGenJob 进 metadata 的 caller 必须显式 import。
"""

from . import auth, channels, companion, conversation, memory, scheduler, settings, system, update, ws

__all__ = ["auth", "channels", "companion", "conversation", "memory", "scheduler", "settings", "system", "update", "ws"]
