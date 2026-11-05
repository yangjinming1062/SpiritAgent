"""全天、离线可用的独立动态决策；扫描周期由调度流程管理。"""

from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger

from features.accounts.queries import list_user_ids
from features.companion.queries import complete_user_ids
from features.posts.publication import request_publication, resume_publications
from features.posts.replies import resume_replies
from features.posts.store import PostBlockedError

logger = get_logger(__name__)


async def resume_autonomous_submissions() -> None:
    await resume_publications()
    await resume_replies()


async def autonomous_post_users() -> list[int]:
    async with SESSION_LOCAL() as db:
        active_users = await list_user_ids(db, active_only=True)
        completed = await complete_user_ids(db, active_users)
    return [user_id for user_id in active_users if user_id in completed]


async def request_autonomous_post(user_id: int) -> None:
    try:
        await request_publication(user_id)
    except PostBlockedError:
        logger.info("Autonomous post skipped by policy or quota", extra={"user_id": user_id})
    except Exception:
        logger.warning("Autonomous post decision failed", extra={"user_id": user_id}, exc_info=True)
