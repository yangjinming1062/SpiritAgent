"""伙伴近况与持续事项只读接口。"""

from typing import Annotated

from fastapi import HTTPException, Path, Query
from features.life.schemas import (
    LifeEventListResponse,
    LifePursuitDetail,
    LifePursuitListResponse,
    LifeStateView,
    LifeStepView,
)
from features.life.use_cases import read_events, read_pursuit, read_pursuits, read_state, read_step
from infrastructure.api_ids import ResourceId
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.http_contracts import operation_policy

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/life", tag="companion-life", remote=True)
_IDENTITIES = operation_policy(identities=("desktop", "remote"))


@router.get("", response_model=LifeStateView, openapi_extra=_IDENTITIES)
async def get_life(user: CurrentUser) -> LifeStateView:
    return await read_state(user.id)


@router.get("/pursuits", response_model=LifePursuitListResponse, openapi_extra=_IDENTITIES)
async def get_life_pursuits(
    user: CurrentUser,
    status: Annotated[str | None, Query(max_length=16)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    include_total: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> LifePursuitListResponse:
    try:
        return await read_pursuits(user.id, status=status, cursor=cursor, limit=limit, include_total=include_total)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/pursuits/{pursuit_id}", response_model=LifePursuitDetail, openapi_extra=_IDENTITIES)
async def get_life_pursuit(
    user: CurrentUser, pursuit_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)]
) -> LifePursuitDetail:
    result = await read_pursuit(user.id, pursuit_id)
    if result is None:
        raise HTTPException(status_code=404, detail="未找到持续事项")
    return result


@router.get("/steps/{step_id}", response_model=LifeStepView, openapi_extra=_IDENTITIES)
async def get_life_step(
    user: CurrentUser, step_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)]
) -> LifeStepView:
    result = await read_step(user.id, step_id)
    if result is None:
        raise HTTPException(status_code=404, detail="未找到生活步骤")
    return result


@router.get("/events", response_model=LifeEventListResponse, openapi_extra=_IDENTITIES)
async def get_life_events(
    user: CurrentUser,
    pursuit_id: Annotated[ResourceId | None, Query(gt=0, le=POSTGRES_INT8_MAX)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    include_total: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> LifeEventListResponse:
    try:
        return await read_events(
            user.id, pursuit_id=pursuit_id, cursor=cursor, limit=limit, include_total=include_total
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
