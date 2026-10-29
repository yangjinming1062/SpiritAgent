"""已分享资讯的独立兴趣反馈，不触发聊天回合。"""

from typing import Annotated

from fastapi import Query
from features.topics import use_cases as topics
from features.topics.schemas import TopicFeedbackRequest, TopicFeedbackSnapshot
from infrastructure.api_ids import ResourceId
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.database import DbSession
from pydantic import Field

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/topics", tag="companion", remote=True)


@router.get("/feedback", response_model=list[TopicFeedbackSnapshot])
async def read_feedback(
    user: CurrentUser,
    db: DbSession,
    topic_ids: list[Annotated[ResourceId, Field(gt=0, le=POSTGRES_INT8_MAX)]] = Query(
        default_factory=list, max_length=100
    ),
) -> list[TopicFeedbackSnapshot]:
    return await topics.read_feedback(db, user.id, topic_ids)


@router.put("/{topic_id}/feedback", response_model=TopicFeedbackSnapshot)
async def update_feedback(
    user: CurrentUser, db: DbSession, body: TopicFeedbackRequest, topic_id: ResourceId
) -> TopicFeedbackSnapshot:
    return await topics.update_feedback(db, user.id, topic_id, body)
