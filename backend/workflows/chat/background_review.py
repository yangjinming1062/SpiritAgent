from features.memory.memory_review import memory_review_backed_off, review_memories
from infrastructure.logger import get_logger
from runtime.memory_scope import MemoryScope

logger = get_logger(__name__)


async def run_background_memory_review(
    scope: MemoryScope,
    *,
    session_id: int,
    through_message_id: int,
) -> None:
    # 该会话的审阅刚失败过：同一批次留待退避期后重试，不每个回合都再付一次模型调用。
    if memory_review_backed_off(scope, session_id):
        logger.info(
            "Background memory review skipped; recent failure is backing off",
            extra={"user_id": scope.user_id, "session_id": session_id},
        )
        return
    try:
        await review_memories(
            scope,
            session_id=session_id,
            through_message_id=through_message_id,
        )
    except Exception:
        logger.warning("Background memory review failed; evidence remains pending", exc_info=True)
