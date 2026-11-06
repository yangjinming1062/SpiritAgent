"""动作素材：语义定义的列表、详情、创建、编辑与删除 REST 端点。"""

from typing import Annotated, Any

from fastapi import Path, Query
from features.action_library import queries as _action_queries
from features.action_library import use_cases as _actions
from features.action_library.interpret import ActionNotFoundError, interpret_action_description
from features.action_library.schemas import (
    ActionAssetDesign,
    ActionAssetUpdate,
    ActionAssetView,
    ActionCreateRequest,
    ActionInterpretation,
    ActionInterpretRequest,
    ActionLibraryGroup,
    ActionListResponse,
    ActionReviewRequest,
    ActionUpdateRequest,
)
from infrastructure.api_ids import ResourceId
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from infrastructure.request_errors import RequestError
from pydantic import ValidationError
from workflows.visuals_companion import design_action_reference, retry_action_reference, update_action_reference

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/actions", tag="companion-actions", remote=True)


@router.get("", response_model=ActionListResponse)
async def list_actions(
    user: CurrentUser,
    db: DbSession,
    query: Annotated[str | None, Query(max_length=200)] = None,
    group: ActionLibraryGroup = "in_library",
    cursor: Annotated[ResourceId | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ActionListResponse:
    return await _action_queries.list_actions(db, user.id, query=query, group=group, cursor=cursor, limit=limit)


@router.get("/{action_id}", response_model=ActionAssetView)
async def get_action(
    action_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser, db: DbSession
) -> ActionAssetView:
    return await _actions.read_action(user_id=user.id, db=db, action_id=action_id)


@router.post("/interpret", response_model=ActionInterpretation, openapi_extra=operation_policy(wait_for_model=True))
async def interpret_action(user: CurrentUser, body: ActionInterpretRequest) -> ActionInterpretation:
    """把用户的一句描述整理成动作定义草稿；只产出草稿，不登记也不评审。"""

    try:
        return await interpret_action_description(user.id, body.description, body.action_id)
    except ActionNotFoundError as exc:
        raise RequestError(status_code=404, message=str(exc)) from exc
    except ValueError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc


def _design(raw: dict[str, Any]) -> ActionAssetDesign:
    try:
        return ActionAssetDesign.model_validate(raw)
    except ValidationError as exc:
        raise RequestError(status_code=422, message="动作定义不符合要求，请重新整理描述") from exc


@router.post("", response_model=ActionAssetView, status_code=202)
async def create_action(user: CurrentUser, db: DbSession, body: ActionCreateRequest) -> ActionAssetView:
    design = _design(body.design)
    try:
        result = await design_action_reference(
            user.id, **design.model_dump(), source="user_requested", request_key=body.request_id
        )
    except ValueError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    return await _actions.read_action(user_id=user.id, db=db, action_id=int(result["action_id"]))


@router.patch("/{action_id}", response_model=ActionAssetView)
async def update_action(
    action_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)],
    user: CurrentUser,
    body: ActionUpdateRequest,
) -> ActionAssetView:
    design = _design(body.design)
    update = ActionAssetUpdate(**design.model_dump(), expected_updated_at=body.expected_updated_at)
    try:
        result = await update_action_reference(user.id, action_id, update)
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


@router.post("/{action_id}/review", response_model=ActionAssetView, status_code=202)
async def retry_review(
    action_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser, body: ActionReviewRequest
) -> ActionAssetView:
    try:
        result = await retry_action_reference(user.id, action_id, body)
    except ValueError as exc:
        raise RequestError(status_code=409, message=str(exc)) from exc
    if result is None:
        raise RequestError(status_code=404, message="未找到动作")
    return result
