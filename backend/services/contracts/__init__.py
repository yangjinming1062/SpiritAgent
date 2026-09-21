"""服务层跨边界词汇：委派动作、记忆作用域与来源。"""

from .delegation import DelegateAction
from .memory import EmbeddingItem, MemoryScope, MemorySource
from .scenes import SceneTurnState

__all__ = ["SceneTurnState", "DelegateAction", "EmbeddingItem", "MemoryScope", "MemorySource"]
