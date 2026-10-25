"""动作素材：语义定义的列表、详情、创建、编辑与删除 REST 端点。"""

from typing import Annotated, Literal

from fastapi import Path, Query
from features.action_library import queries as _action_queries
from features.action_library import use_cases as _actions
from features.action_library.schemas import ActionAssetDesign, ActionAssetUpdate, ActionAssetView, ActionListResponse
from infrastructure.api_ids import ResourceId
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError
from workflows.visuals_companion import design_action_reference, update_action_reference

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/actions", tag="companion-actions", remote=True)


@router.get("", response_model=ActionListResponse)
async def list_actions(
    user: CurrentUser,
    db: DbSession,
    query: Annotated[str | None, Query(max_length=200)] = None,
    clip_kind: Annotated[Literal["once", "loop"] | None, Query()] = None,
    status: Annotated[str | None, Query(max_length=24)] = None,
    cursor: Annotated[ResourceId | None, Query()] = None,
    include_total: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ActionListResponse:
    return await _action_queries.list_actions(
        db,
        user.id,
        query=query,
        clip_kind=clip_kind,
        status=status,
        cursor=cursor,
        include_total=include_total,
        limit=limit,
    )


@router.get("/{action_id}", response_model=ActionAssetView)
async def get_action(
    action_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser, db: DbSession
) -> ActionAssetView:
    return await _actions.read_action(user_id=user.id, db=db, action_id=action_id)


@router.post("", response_model=ActionAssetView, status_code=202)
async def create_action(user: CurrentUser, db: DbSession, body: ActionAssetDesign) -> ActionAssetView:
    try:
        result = await design_action_reference(
            user.id,
            name=body.name,
            motion_description=body.motion_description,
            use_when=body.use_when,
            avoid_when=body.avoid_when,
            allowed_triggers=body.allowed_triggers,
            reason=body.reason,
            duration_seconds=body.duration_seconds,
            clip_kind=body.clip_kind,
            source="user_requested",
        )
    except ValueError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    return await _actions.read_action(user_id=user.id, db=db, action_id=int(result["action_id"]))


@router.patch("/{action_id}", response_model=ActionAssetView)
async def update_action(
    action_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser, body: ActionAssetUpdate
) -> ActionAssetView:
    try:
        result = await update_action_reference(user.id, action_id, body)
    except ValueError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    if result is None:
        raise RequestError(status_code=404, message="未找到动作素材")
    return result


@router.delete("/{action_id}", status_code=204)
async def delete_action(
    action_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser, db: DbSession
) -> None:
    await _actions.delete_action(user_id=user.id, db=db, action_id=action_id)
