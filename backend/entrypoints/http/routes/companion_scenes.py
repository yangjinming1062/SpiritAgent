"""伙伴场景资产与当前环境的 REST 端点。"""

from fastapi import HTTPException, Query
from features.appearance.schemas import ImageAdoptRequest
from features.companion.schemas import CompanionOperationResponse
from features.scenes import http_use_cases as _scenes
from features.scenes.schemas import (
    SceneActivateRequest,
    SceneDescriptionRequest,
    SceneGenerateRequest,
    SceneListResponse,
    ScenePolicyRequest,
    ScenePolicyResponse,
    ScenePromptRequest,
    SceneResponse,
    SceneStateResponse,
)
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion", tag="companion")


@router.get("/scenes/state", response_model=SceneStateResponse)
async def read_scene_state(user: CurrentUser, db: DbSession) -> SceneStateResponse:
    try:
        return await _scenes.read_scene_state(user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/generate", response_model=SceneResponse, status_code=202)
async def post_scene_generate(user: CurrentUser, body: SceneGenerateRequest) -> SceneResponse:
    try:
        return await _scenes.post_scene_generate(user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/prompt", response_model=SceneResponse, status_code=202)
async def post_scene_prompt(user: CurrentUser, body: ScenePromptRequest) -> SceneResponse:
    try:
        return await _scenes.post_scene_prompt(user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/{scene_id}/adopt", response_model=SceneResponse)
async def post_scene_adopt(user: CurrentUser, scene_id: int, body: ImageAdoptRequest) -> SceneResponse:
    try:
        return await _scenes.post_scene_adopt(user_id=user.id, scene_id=scene_id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/adopt", response_model=SceneResponse)
async def post_scene_upload(user: CurrentUser, body: ImageAdoptRequest) -> SceneResponse:
    try:
        return await _scenes.post_scene_upload(user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/{scene_id}/discard", response_model=SceneResponse)
async def post_scene_discard(user: CurrentUser, scene_id: int) -> SceneResponse:
    try:
        return await _scenes.post_scene_discard(user_id=user.id, scene_id=scene_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/{scene_id}/regenerate", response_model=SceneResponse, status_code=202)
async def post_scene_regenerate(user: CurrentUser, scene_id: int) -> SceneResponse:
    try:
        return await _scenes.post_scene_regenerate(user_id=user.id, scene_id=scene_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/scenes/{scene_id}", response_model=CompanionOperationResponse)
async def delete_scene_route(user: CurrentUser, scene_id: int) -> CompanionOperationResponse:
    try:
        return await _scenes.delete_scene_route(user_id=user.id, scene_id=scene_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/activate", response_model=SceneResponse)
async def post_scene_activate(user: CurrentUser, db: DbSession, body: SceneActivateRequest) -> SceneResponse:
    try:
        return await _scenes.post_scene_activate(user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/scenes/policy", response_model=ScenePolicyResponse)
async def patch_scene_policy(user: CurrentUser, db: DbSession, body: ScenePolicyRequest) -> ScenePolicyResponse:
    try:
        return await _scenes.patch_scene_policy(user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/scenes", response_model=SceneListResponse)
async def scene_list_route(
    user: CurrentUser,
    db: DbSession,
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
) -> SceneListResponse:
    try:
        return await _scenes.scene_list_route(user_id=user.id, db=db, q=q, offset=offset, limit=limit)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/scenes/{scene_id}", response_model=SceneResponse)
async def scene_detail_route(user: CurrentUser, db: DbSession, scene_id: int) -> SceneResponse:
    try:
        return await _scenes.scene_detail_route(user_id=user.id, db=db, scene_id=scene_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/scenes/{scene_id}", response_model=SceneResponse)
async def scene_edit_route(user: CurrentUser, scene_id: int, body: SceneDescriptionRequest) -> SceneResponse:
    try:
        return await _scenes.scene_edit_route(user_id=user.id, scene_id=scene_id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scenes/{scene_id}/analyze", response_model=SceneResponse, status_code=202)
async def scene_analyze_route(user: CurrentUser, scene_id: int) -> SceneResponse:
    try:
        return await _scenes.scene_analyze_route(user_id=user.id, scene_id=scene_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
