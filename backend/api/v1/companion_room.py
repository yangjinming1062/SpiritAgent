"""伙伴房间图 REST 端点。

挂在 /api/companion/room/*；GET /room 必须足够让 Client 冷启动画出房间。
"""

from common import get_router
from components import DbSession, get_logger
from fastapi import HTTPException
from modules.auth import CurrentUser
from modules.companion import (
    BackdropPolicyRequest,
    BackdropPolicyResponse,
    BackdropResponse,
    RoomActivateRequest,
    RoomGenerateRequest,
    RoomStateResponse,
)
from services.companion import (
    BackdropOrigin,
    RoomBackdropError,
    RoomBackdropNotFoundError,
    RoomBackdropStateError,
    activate_backdrop,
    get_backdrop,
    get_room_state,
    response_for_backdrop,
    schedule_room_generation,
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
        active=BackdropResponse(**response_for_backdrop(state["active"])) if state["active"] is not None else None,
        history=[BackdropResponse(**response_for_backdrop(r)) for r in state["history"]],
        policy=state["policy"],
        pending=BackdropResponse(**response_for_backdrop(state["pending"])) if state["pending"] is not None else None,
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
        )
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


@router.get("/room/{backdrop_id}", response_model=BackdropResponse)
async def get_room_by_id(
    user: CurrentUser,
    db: DbSession,
    backdrop_id: int,
) -> BackdropResponse:
    row = await get_backdrop(db, user.id, backdrop_id)
    if row is None:
        raise HTTPException(status_code=404, detail="找不到对应的房间图")
    return BackdropResponse(**response_for_backdrop(row))
