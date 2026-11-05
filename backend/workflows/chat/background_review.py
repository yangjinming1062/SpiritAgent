from features.memory.memory_review import review_memories
from infrastructure.logger import get_logger
from runtime.memory_scope import MemoryScope

logger = get_logger(__name__)


async def run_background_memory_review(scope: MemoryScope, *, session_id: int, through_message_id: int) -> None:
    try:
        await review_memories(scope, session_id=session_id, through_message_id=through_message_id)
    except Exception:
        logger.warning("Background memory review failed; evidence remains pending", exc_info=True)
