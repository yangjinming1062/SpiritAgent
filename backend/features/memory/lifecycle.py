"""记忆索引与审阅运行镜像的生命周期。"""

from runtime.lifecycle import RuntimeParticipant

from .memory_review import invalidate_memory_review_locks
from .memory_store import drain_memory_embeddings


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant("memory.embeddings", drain=drain_memory_embeddings),
        RuntimeParticipant("memory.review_locks", invalidate_user=invalidate_memory_review_locks),
    )
