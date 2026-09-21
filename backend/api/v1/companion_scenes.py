"""伙伴场景资产与当前环境的 REST 端点。"""

import base64

from common import get_router
from components import DbSession, get_logger
from fastapi import HTTPException, Query
from modules.auth import CurrentUser
from modules.companion import (
    CompanionOperationResponse,
    ImageAdoptRequest,
    SceneActivateRequest,
    SceneDescriptionRequest,
    SceneGenerateRequest,
    SceneListResponse,
    SceneOrigin,
    ScenePolicyRequest,
    ScenePolicyResponse,
    ScenePromptRequest,
    SceneResponse,
    SceneStateResponse,
)
from services.application.generation import (
    SceneError,
    SceneNotFoundError,
    SceneStateError,
    activate_scene,
    adopt_scene,
    delete_scene,
    discard_scene,
    edit_scene_description,
    retry_scene_description,
    schedule_scene_generation,
    schedule_scene_prompt,
    set_scene_policy,
)
from services.domains.companion import get_scene, get_scene_state, list_scenes, response_for_scene

router = get_router(prefix="/api/companion", tag="companion")
logger = get_logger(__name__)


@router.get("/scenes/state", response_model=SceneStateResponse)
async def read_scene_state(
    user: CurrentUser,
    db: DbSession,
) -> SceneStateResponse:
    state = await get_scene_state(db, user.id)
    return SceneStateResponse(
        active=SceneResponse(**response_for_scene(state.active)) if state.active is not None else None,
        version=state.version,
        switch_version=state.switch_version,
        policy=state.policy,
        pending=SceneResponse(**response_for_scene(state.pending)) if state.pending is not None else None,
    )


@router.post("/scenes/generate", response_model=SceneResponse, status_code=202)
async def post_scene_generate(
    user: CurrentUser,
    body: SceneGenerateRequest,
) -> SceneResponse:
    try:
        row = await schedule_scene_generation(
            user.id,
            origin=SceneOrigin.USER_REQUEST.value,
            notes=body.notes,
            outfit_description=body.outfit_description,
            reference_image=f"data:{body.content_type};base64,{body.image}" if body.image is not None else None,
        )
    except SceneStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except SceneError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return SceneResponse(**response_for_scene(row))


@router.post("/scenes/prompt", response_model=SceneResponse, status_code=202)
async def post_scene_prompt(
    user: CurrentUser,
    body: ScenePromptRequest,
) -> SceneResponse:
    try:
        row = await schedule_scene_prompt(user.id, notes=body.notes, outfit_description=body.outfit_description)
    except SceneStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except SceneError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return SceneResponse(**response_for_scene(row))


@router.post("/scenes/{scene_id}/adopt", response_model=SceneResponse)
async def post_scene_adopt(
    user: CurrentUser,
    scene_id: int,
    body: ImageAdoptRequest,
) -> SceneResponse:
    return await _adopt_scene_image(user.id, scene_id, body)


@router.post("/scenes/adopt", response_model=SceneResponse)
async def post_scene_upload(user: CurrentUser, body: ImageAdoptRequest) -> SceneResponse:
    return await _adopt_scene_image(user.id, None, body)


async def _adopt_scene_image(user_id: int, scene_id: int | None, body: ImageAdoptRequest) -> SceneResponse:
    try:
        data = base64.b64decode(body.image, validate=True)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    try:
        row = await adopt_scene(user_id, scene_id, data=data)
    except SceneNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的场景", "reason": str(exc)})
    except SceneStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except SceneError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return SceneResponse(**response_for_scene(row))


@router.post("/scenes/{scene_id}/discard", response_model=SceneResponse)
async def post_scene_discard(
    user: CurrentUser,
    scene_id: int,
) -> SceneResponse:
    try:
        row = await discard_scene(user.id, scene_id)
    except SceneNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的场景", "reason": str(exc)})
    except SceneStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except SceneError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return SceneResponse(**response_for_scene(row))


@router.delete("/scenes/{scene_id}", response_model=CompanionOperationResponse)
async def delete_scene_route(
    user: CurrentUser,
    scene_id: int,
) -> CompanionOperationResponse:
    try:
        await delete_scene(user.id, scene_id)
    except SceneNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的场景", "reason": str(exc)})
    except SceneStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except SceneError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return CompanionOperationResponse(ok=True)


@router.post("/scenes/activate", response_model=SceneResponse)
async def post_scene_activate(
    user: CurrentUser,
    db: DbSession,
    body: SceneActivateRequest,
) -> SceneResponse:
    try:
        row = await activate_scene(db, user.id, body.scene_id)
    except SceneNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的场景", "reason": str(exc)})
    except SceneStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except SceneError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return SceneResponse(**response_for_scene(row))


@router.patch("/scenes/policy", response_model=ScenePolicyResponse)
async def patch_scene_policy(
    user: CurrentUser,
    db: DbSession,
    body: ScenePolicyRequest,
) -> ScenePolicyResponse:
    try:
        policy = await set_scene_policy(db, user.id, body.policy)
    except SceneError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return ScenePolicyResponse(policy=policy)


@router.get("/scenes", response_model=SceneListResponse)
async def scene_list_route(
    user: CurrentUser,
    db: DbSession,
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
) -> SceneListResponse:
    state = await get_scene_state(db, user.id)
    rows, total = await list_scenes(db, user.id, query=q, offset=offset, limit=limit)
    return SceneListResponse(
        scenes=[SceneResponse(**response_for_scene(row)) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
        version=state.version,
    )


@router.get("/scenes/{scene_id}", response_model=SceneResponse)
async def scene_detail_route(user: CurrentUser, db: DbSession, scene_id: int) -> SceneResponse:
    row = await get_scene(db, user.id, scene_id)
    if row is None:
        raise HTTPException(status_code=404, detail="找不到对应场景")
    return SceneResponse(**response_for_scene(row))


@router.patch("/scenes/{scene_id}", response_model=SceneResponse)
async def scene_edit_route(user: CurrentUser, scene_id: int, body: SceneDescriptionRequest) -> SceneResponse:
    try:
        row = await edit_scene_description(user.id, scene_id, body)
    except SceneNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SceneError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return SceneResponse(**response_for_scene(row))


@router.post("/scenes/{scene_id}/analyze", response_model=SceneResponse, status_code=202)
async def scene_analyze_route(user: CurrentUser, scene_id: int) -> SceneResponse:
    try:
        row = await retry_scene_description(user.id, scene_id)
    except SceneNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SceneError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return SceneResponse(**response_for_scene(row))
