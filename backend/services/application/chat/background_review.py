from components import get_logger

from services.contracts import MemoryScope
from services.domains.memory import memory_review_backed_off, review_memories
from services.infrastructure.llm import UserLlmConfig

logger = get_logger(__name__)


async def run_background_memory_review(
    scope: MemoryScope,
    llm_config: UserLlmConfig,
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
            llm_config=llm_config,
        )
    except Exception:
        logger.warning("Background memory review failed; evidence remains pending", exc_info=True)
