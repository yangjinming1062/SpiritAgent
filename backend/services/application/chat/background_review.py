from components import get_logger

from services.contracts import MemoryScope
from services.domains.memory import review_memories
from services.infrastructure.llm import UserLlmConfig

logger = get_logger(__name__)


async def run_background_memory_review(
    scope: MemoryScope,
    llm_config: UserLlmConfig,
    *,
    session_id: int,
    through_message_id: int,
) -> None:
    try:
        await review_memories(
            scope,
            session_id=session_id,
            through_message_id=through_message_id,
            llm_config=llm_config,
        )
    except Exception:
        logger.warning("Background memory review failed; evidence remains pending", exc_info=True)
