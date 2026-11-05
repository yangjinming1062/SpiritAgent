"""媒体任务状态查询受理；业务视图经组合根装配。"""

from collections.abc import Awaitable, Callable

from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError

from features.jobs.service import owned_job_kind

from .continuations import available_media_actions
from .review_views import MediaJobView

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
    view.continuation_actions = await available_media_actions(user_id, job_id)
    return view
