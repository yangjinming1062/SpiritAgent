import sys

from components import SETTINGS
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def get_router(*, prefix: str | None = None, tag: str | None = None, dependencies: list | None = None) -> APIRouter:
    """默认 prefix 由 caller 模块的 leaf 名推导为 ``/api/<resource>``；传 ``prefix=""`` 挂载到根（admin 页面 router 用）。"""
    resource = sys._getframe(1).f_globals["__name__"].rsplit(".", 1)[-1]
    return APIRouter(
        prefix=f"{SETTINGS.api_prefix}/{resource}" if prefix is None else prefix,
        tags=[tag or resource],
        dependencies=dependencies,
    )


async def get_or_404[M](db: AsyncSession, model: type[M], /, detail: str, **filters: object) -> M:
    obj = (await db.execute(select(model).filter_by(**filters))).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return obj
