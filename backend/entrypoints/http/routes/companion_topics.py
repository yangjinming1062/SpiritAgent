"""已分享资讯的独立兴趣反馈，不触发聊天回合。"""

from fastapi import HTTPException, Path, Query
from features.topics import use_cases as topics
from features.topics.schemas import TopicFeedbackRequest, TopicFeedbackSnapshot
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/topics", tag="companion")


@router.get("/feedback", response_model=list[TopicFeedbackSnapshot])
async def read_feedback(
    user: CurrentUser,
    db: DbSession,
    topic_ids: list[int] = Query(default=[], max_length=100),
) -> list[TopicFeedbackSnapshot]:
    if any(topic_id <= 0 for topic_id in topic_ids):
        raise HTTPException(status_code=422, detail="Invalid topic IDs")
    try:
        return await topics.read_feedback(db, user.id, topic_ids)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.put("/{topic_id}/feedback", response_model=TopicFeedbackSnapshot)
async def update_feedback(
    user: CurrentUser,
    db: DbSession,
    body: TopicFeedbackRequest,
    topic_id: int = Path(ge=1),
) -> TopicFeedbackSnapshot:
    try:
        return await topics.update_feedback(db, user.id, topic_id, body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
