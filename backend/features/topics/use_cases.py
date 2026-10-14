"""已分享话题反馈的请求事务。"""

from collections.abc import Sequence

from runtime.memory_scope import MemoryScope
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import TopicFeedbackRequest, TopicFeedbackSnapshot
from .store import get_topic_feedback, set_topic_feedback


async def read_feedback(db: AsyncSession, user_id: int, topic_ids: Sequence[int]) -> list[TopicFeedbackSnapshot]:
    return await get_topic_feedback(db, MemoryScope(user_id, "companion"), topic_ids)


async def update_feedback(
    db: AsyncSession,
    user_id: int,
    topic_id: int,
    body: TopicFeedbackRequest,
) -> TopicFeedbackSnapshot:
    result = await set_topic_feedback(db, MemoryScope(user_id, "companion"), topic_id, body.value)
    await db.commit()
    return result
