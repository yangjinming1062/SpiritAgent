from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.request_errors import RequestError


async def get_or_404[M](db: AsyncSession, model: type[M], /, detail: str, **filters: object) -> M:
    obj = (await db.execute(select(model).filter_by(**filters))).scalar_one_or_none()
    if obj is None:
        raise RequestError(status_code=404, detail=detail)
    return obj
