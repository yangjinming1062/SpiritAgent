"""动作素材的 HTTP 用例薄壳：领域异常转换为请求错误。"""

from infrastructure.request_errors import RequestError
from sqlalchemy.ext.asyncio import AsyncSession

from . import queries, service
from .review import cancel_action_reviews
from .schemas import ActionAssetView


def _not_found() -> RequestError:
    return RequestError(status_code=404, code="not_found", message="未找到该动作素材")


async def read_action(user_id: int, db: AsyncSession, *, action_id: int) -> ActionAssetView:
    view = await queries.action_detail(db, user_id, action_id)
    if view is None:
        raise _not_found()
    return view


async def delete_action(user_id: int, db: AsyncSession, *, action_id: int) -> None:
    try:
        deleted = await service.delete_action(db, user_id, action_id)
    except ValueError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    if not deleted:
        raise _not_found()
    await db.commit()
    await cancel_action_reviews(user_id, action_id)
