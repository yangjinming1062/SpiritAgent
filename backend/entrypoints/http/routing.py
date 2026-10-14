import sys

from fastapi import APIRouter, HTTPException
from infrastructure.queries import get_or_404 as query_or_404
from infrastructure.request_errors import RequestError
from sqlalchemy.ext.asyncio import AsyncSession


def get_router(*, prefix: str | None = None, tag: str | None = None, dependencies: list | None = None) -> APIRouter:
    """默认 prefix 由 caller 模块的 leaf 名推导为 ``/api/<resource>``；传 ``prefix=""`` 挂载到根（admin 页面 router 用）。"""
    resource = sys._getframe(1).f_globals["__name__"].rsplit(".", 1)[-1]
    return APIRouter(
        prefix=f"/api/{resource}" if prefix is None else prefix,
        tags=[tag or resource],
        dependencies=dependencies,
    )


async def get_or_404[M](db: AsyncSession, model: type[M], /, detail: str, **filters: object) -> M:
    try:
        return await query_or_404(db, model, detail=detail, **filters)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
