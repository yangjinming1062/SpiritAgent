from . import auth, channels, companion, conversation, memory, scheduler, settings, system, update, ws

# 导入每个子包会注册其 ORM 模型到 ModelBase.metadata，使 create_all 看到所有表；modules.media 故意省略——其 mapper 在陈旧 DB 连接上会触发 import-time crash，需要 VideoGenJob 进 create_all 的 caller 必须显式 import。

__all__ = ["auth", "channels", "companion", "conversation", "memory", "scheduler", "settings", "system", "update", "ws"]
