"""伙伴环境素材的 REST 端点；素材本身不表示当前呈现环境。"""

from typing import Annotated

from fastapi import Body, Path, Query
from features.companion.schemas import CompanionOperationResponse
from features.scenes import http_use_cases as _scenes
from features.scenes.schemas import (
    SceneDescriptionRequest,
    SceneGenerateRequest,
    SceneImportRequest,
    SceneListResponse,
    ScenePolicyRequest,
    ScenePolicyResponse,
    ScenePromptRequest,
    SceneResponse,
    SceneStateResponse,
)
from infrastructure.api_ids import ResourceId
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion", tag="companion", remote=True)


@router.get("/scenes/state", response_model=SceneStateResponse)
async def read_scene_state(user: CurrentUser, db: DbSession) -> SceneStateResponse:
    return await _scenes.read_scene_state(user_id=user.id, db=db)


@router.post("/scenes", response_model=SceneResponse, status_code=202, openapi_extra=operation_policy(upload="image"))
async def create_scene(
    user: CurrentUser, body: Annotated[SceneGenerateRequest | SceneImportRequest, Body(discriminator="source")]
) -> SceneResponse:
    return await _scenes.create_scene(user_id=user.id, body=body)


@router.post("/scenes/prompt", response_model=SceneResponse, status_code=202)
async def post_scene_prompt(user: CurrentUser, body: ScenePromptRequest) -> SceneResponse:
    return await _scenes.post_scene_prompt(user_id=user.id, body=body)


@router.post("/scenes/{scene_id}/discard", response_model=SceneResponse)
async def post_scene_discard(
    user: CurrentUser, scene_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)]
) -> SceneResponse:
    return await _scenes.post_scene_discard(user_id=user.id, scene_id=scene_id)


@router.post("/scenes/{scene_id}/regenerate", response_model=SceneResponse, status_code=202)
async def post_scene_regenerate(
    user: CurrentUser, scene_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)]
) -> SceneResponse:
    return await _scenes.post_scene_regenerate(user_id=user.id, scene_id=scene_id)


@router.delete("/scenes/{scene_id}", response_model=CompanionOperationResponse)
async def delete_scene_route(
    user: CurrentUser, scene_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)]
) -> CompanionOperationResponse:
    return await _scenes.delete_scene_route(user_id=user.id, scene_id=scene_id)


@router.patch("/scenes/policy", response_model=ScenePolicyResponse)
async def patch_scene_policy(user: CurrentUser, db: DbSession, body: ScenePolicyRequest) -> ScenePolicyResponse:
    return await _scenes.patch_scene_policy(user_id=user.id, db=db, body=body)


@router.get("/scenes", response_model=SceneListResponse)
async def scene_list_route(
    user: CurrentUser,
    db: DbSession,
    q: str = Query(default="", max_length=200, pattern=r"^[^\x00]*$"),
    cursor: ResourceId | None = Query(default=None),
    include_total: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
) -> SceneListResponse:
    return await _scenes.scene_list_route(
        user_id=user.id, db=db, q=q, cursor=cursor, include_total=include_total, limit=limit
    )


@router.get("/scenes/{scene_id}", response_model=SceneResponse)
async def scene_detail_route(
    user: CurrentUser, db: DbSession, scene_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)]
) -> SceneResponse:
    return await _scenes.scene_detail_route(user_id=user.id, db=db, scene_id=scene_id)


@router.patch("/scenes/{scene_id}", response_model=SceneResponse)
async def scene_edit_route(
    user: CurrentUser, scene_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], body: SceneDescriptionRequest
) -> SceneResponse:
    return await _scenes.scene_edit_route(user_id=user.id, scene_id=scene_id, body=body)


@router.post("/scenes/{scene_id}/analyze", response_model=SceneResponse, status_code=202)
async def scene_analyze_route(
    user: CurrentUser, scene_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)]
) -> SceneResponse:
    return await _scenes.scene_analyze_route(user_id=user.id, scene_id=scene_id)
