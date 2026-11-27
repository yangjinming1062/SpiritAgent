"""媒体任务状态查询受理；业务视图经组合根装配。"""

from collections.abc import Awaitable, Callable

from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError

from features.jobs.service import owned_job_control, owned_job_kind

from .continuations import available_media_actions, continue_manual_outfit_waits
from .queries import conversation_video_job
from .review_views import MediaContinueRequest, MediaJobView

type ReviewQuery = Callable[[int, int], Awaitable[MediaJobView]]
_HANDLERS: dict[str, ReviewQuery] = {}


def configure_review_handler(kind: str, query: ReviewQuery) -> None:
    _HANDLERS[kind] = query


async def _handler(user_id: int, job_id: int) -> ReviewQuery:
    async with SESSION_LOCAL() as db:
        kind = await owned_job_kind(db, user_id, job_id)
    if kind not in _HANDLERS:
        raise RequestError(status_code=404, message="找不到可检查的媒体任务")
    return _HANDLERS[kind]


async def query_media_job(user_id: int, job_id: int) -> MediaJobView:
    query = await _handler(user_id, job_id)
    view = await query(user_id, job_id)
    if view.kind in {"media.image", "media.video"}:
        for slot in view.slots:
            slot.review = None
        view.image_request = None
        view.bindings = {}
        return view
    if view.kind == "outfit.create" and view.state == "paused" and view.stage == "confirmation":
        view.continuation_actions = await available_media_actions(user_id, job_id)
    return view


async def continue_media_job(user_id: int, job_id: int, request: MediaContinueRequest) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        job = await owned_job_control(db, user_id, job_id, lock=True)
        if (
            job is None
            or job.kind not in _HANDLERS
            or (job.kind == "media.video" and await conversation_video_job(db, user_id, job_id) is None)
        ):
            raise RequestError(status_code=404, message="找不到可检查的媒体任务")
        if job.revision != request.expected_revision:
            raise RequestError(status_code=409, message="任务状态已变化，请刷新")
        awaiting_save = (job.kind, job.state, job.stage) == ("outfit.create", "paused", "confirmation")
        if not awaiting_save or not await continue_manual_outfit_waits(db, user_id, job_id):
            raise RequestError(status_code=409, message="当前任务没有这项可用操作")
        await db.commit()
    updated = await query_media_job(user_id, job_id)
    if updated.revision == request.expected_revision and updated.state == "paused":
        raise RequestError(status_code=409, message="原授权或检查条件尚不满足，结果已保留")
    return updated
