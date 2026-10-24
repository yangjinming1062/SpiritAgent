"""伙伴近况与持续事项只读接口。"""

from typing import Annotated

from fastapi import HTTPException, Path, Query
from features.life.schemas import LifeEventListResponse, LifePursuitListResponse, LifePursuitView, LifeStateView
from features.life.use_cases import read_events, read_pursuit, read_pursuits, read_state
from infrastructure.constants import POSTGRES_INT4_MAX
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
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> LifePursuitListResponse:
    return await read_pursuits(user.id, status=status, offset=offset, limit=limit)


@router.get("/pursuits/{pursuit_id}", response_model=LifePursuitView, openapi_extra=_IDENTITIES)
async def get_life_pursuit(
    user: CurrentUser,
    pursuit_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
) -> LifePursuitView:
    result = await read_pursuit(user.id, pursuit_id)
    if result is None:
        raise HTTPException(status_code=404, detail="未找到持续事项")
    return result


@router.get("/events", response_model=LifeEventListResponse, openapi_extra=_IDENTITIES)
async def get_life_events(
    user: CurrentUser,
    pursuit_id: Annotated[int | None, Query(gt=0, le=POSTGRES_INT4_MAX)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> LifeEventListResponse:
    return await read_events(user.id, pursuit_id=pursuit_id, offset=offset, limit=limit)
