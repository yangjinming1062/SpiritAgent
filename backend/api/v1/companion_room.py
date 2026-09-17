"""伙伴房间图 REST 端点。

挂在 /api/companion/room/*；GET /room 必须足够让 Client 冷启动画出房间。
"""

import base64

from common import get_router
from components import DbSession, get_logger
from fastapi import HTTPException
from modules.auth import CurrentUser
from modules.companion import (
    BackdropPolicyRequest,
    BackdropPolicyResponse,
    BackdropResponse,
    ImageAdoptRequest,
    RoomActivateRequest,
    RoomGenerateRequest,
    RoomPromptRequest,
    RoomStateResponse,
)
from services.application.generation import (
    BackdropOrigin,
    RoomBackdropError,
    RoomBackdropNotFoundError,
    RoomBackdropStateError,
    activate_backdrop,
    adopt_room_backdrop,
    discard_room_backdrop,
    get_room_state,
    response_for_backdrop,
    schedule_room_generation,
    schedule_room_prompt,
    set_backdrop_policy,
)

router = get_router(prefix="/api/companion", tag="companion")
logger = get_logger(__name__)


@router.get("/room", response_model=RoomStateResponse)
async def get_room(
    user: CurrentUser,
    db: DbSession,
) -> RoomStateResponse:
    state = await get_room_state(db, user.id)
    return RoomStateResponse(
        active=BackdropResponse(**response_for_backdrop(state.active)) if state.active is not None else None,
        history=[BackdropResponse(**response_for_backdrop(r)) for r in state.history],
        policy=state.policy,
        pending=BackdropResponse(**response_for_backdrop(state.pending)) if state.pending is not None else None,
    )


@router.post("/room/generate", response_model=BackdropResponse, status_code=202)
async def post_room_generate(
    user: CurrentUser,
    body: RoomGenerateRequest,
) -> BackdropResponse:
    try:
        row = await schedule_room_generation(
            user.id,
            origin=BackdropOrigin.USER_REQUEST.value,
            intent=body.intent,
            notes=body.notes,
            reference_image=f"data:{body.content_type};base64,{body.image}" if body.image is not None else None,
        )
    except RoomBackdropStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except RoomBackdropError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return BackdropResponse(**response_for_backdrop(row))


@router.post("/room/prompt", response_model=BackdropResponse, status_code=202)
async def post_room_prompt(
    user: CurrentUser,
    body: RoomPromptRequest,
) -> BackdropResponse:
    """自备图第一步：创建等待上传的 pending 行并落库提示词；不启动生图任务。"""
    try:
        row = await schedule_room_prompt(user.id, intent=body.intent.value, notes=body.notes)
    except RoomBackdropStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except RoomBackdropError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return BackdropResponse(**response_for_backdrop(row))


@router.post("/room/{backdrop_id}/adopt", response_model=BackdropResponse)
async def post_room_adopt(
    user: CurrentUser,
    backdrop_id: int,
    body: ImageAdoptRequest,
) -> BackdropResponse:
    """自备图采纳：等待上传的行按生成链同一激活/事件语义转 ready。"""
    try:
        data = base64.b64decode(body.image, validate=True)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    try:
        row = await adopt_room_backdrop(user.id, backdrop_id, data=data)
    except RoomBackdropNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的房间图", "reason": str(exc)})
    except RoomBackdropStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except RoomBackdropError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return BackdropResponse(**response_for_backdrop(row))


@router.post("/room/{backdrop_id}/discard", response_model=BackdropResponse)
async def post_room_discard(
    user: CurrentUser,
    backdrop_id: int,
) -> BackdropResponse:
    """放弃等待上传的自备图行。"""
    try:
        row = await discard_room_backdrop(user.id, backdrop_id)
    except RoomBackdropNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的房间图", "reason": str(exc)})
    except RoomBackdropStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except RoomBackdropError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return BackdropResponse(**response_for_backdrop(row))


@router.post("/room/activate", response_model=BackdropResponse)
async def post_room_activate(
    user: CurrentUser,
    db: DbSession,
    body: RoomActivateRequest,
) -> BackdropResponse:
    try:
        row = await activate_backdrop(db, user.id, body.backdrop_id)
    except RoomBackdropNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "找不到对应的房间图", "reason": str(exc)})
    except RoomBackdropStateError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "reason": str(exc)})
    except RoomBackdropError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "reason": str(exc)})
    return BackdropResponse(**response_for_backdrop(row))


@router.patch("/room/policy", response_model=BackdropPolicyResponse)
async def patch_room_policy(
    user: CurrentUser,
    db: DbSession,
    body: BackdropPolicyRequest,
) -> BackdropPolicyResponse:
    policy = await set_backdrop_policy(db, user.id, body.policy)
    return BackdropPolicyResponse(policy=policy)
