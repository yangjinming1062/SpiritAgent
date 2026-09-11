import inspect
from collections.abc import Iterable
from typing import Any

from components import SETTINGS
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def get_router(*, prefix: str | None = None, tag: str | None = None, dependencies: list | None = None) -> APIRouter:
    """默认 prefix 由 caller 模块的 leaf 名推导为 ``/api/<resource>``；传 ``prefix=""`` 挂载到根（admin 页面 router 用）。"""
    resource = inspect.currentframe().f_back.f_globals["__name__"].rsplit(".", 1)[-1]
    if prefix is None:
        prefix = f"{SETTINGS.api_prefix}/{resource}"
    if tag is None:
        tag = resource
    kwargs: dict[str, Any] = {"prefix": prefix, "tags": [tag]}
    if dependencies is not None:
        kwargs["dependencies"] = dependencies
    return APIRouter(**kwargs)


def list_response(records: Iterable[Any], item_cls: type[BaseModel], response_cls: type[BaseModel]) -> BaseModel:
    return response_cls(items=[item_cls.model_validate(r) for r in records])


async def get_or_404(db: AsyncSession, model: type, /, detail: str | None = None, **filters) -> Any:
    obj = (await db.execute(select(model).filter_by(**filters))).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail or f"{model.__name__} not found")
    return obj
