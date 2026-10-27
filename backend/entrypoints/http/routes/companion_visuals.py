"""影像库：成品浏览、预览、重做与制作工作项的 REST 端点。"""

from typing import Annotated, Literal

from fastapi import Path, Query
from features.visuals import queries as _visual_queries
from features.visuals import use_cases as _visuals
from features.visuals.schemas import (
    ActionMediaListResponse,
    StageReceipt,
    VisualAssetKind,
    VisualClaimRequest,
    VisualClaimResponse,
    VisualDetailView,
    VisualJobCommandRequest,
    VisualJobListResponse,
    VisualJobState,
    VisualJobView,
    VisualListResponse,
    VisualPlayCommand,
    VisualPreferencesRequest,
    VisualPresentationResponse,
    VisualReceiptRequest,
    VisualReceiptResponse,
    VisualRegenerateRequest,
)
from features.visuals.static_presentation import record_static_receipt
from infrastructure.api_ids import ResourceId
from infrastructure.constants import POSTGRES_INT8_MAX
from infrastructure.http_contracts import operation_policy

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/visuals", tag="companion-visuals", remote=True)


@router.get("", response_model=VisualListResponse)
async def list_visuals(
    user: CurrentUser,
    query: Annotated[str | None, Query(max_length=200)] = None,
    media_kind: Annotated[Literal["image", "video"] | None, Query()] = None,
    asset_kind: Annotated[VisualAssetKind | None, Query()] = None,
    outfit_id: Annotated[ResourceId | None, Query(gt=0, le=POSTGRES_INT8_MAX)] = None,
    scene_id: Annotated[ResourceId | None, Query(gt=0, le=POSTGRES_INT8_MAX)] = None,
    action_id: Annotated[ResourceId | None, Query(gt=0, le=POSTGRES_INT8_MAX)] = None,
    source: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[ResourceId | None, Query()] = None,
    include_total: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> VisualListResponse:
    return await _visual_queries.list_visuals(
        user_id=user.id,
        query=query,
        media_kind=media_kind,
        asset_kind=asset_kind,
        outfit_id=outfit_id,
        scene_id=scene_id,
        action_id=action_id,
        source=source,
        cursor=cursor,
        include_total=include_total,
        limit=limit,
    )


@router.get("/jobs", response_model=VisualJobListResponse)
async def list_visual_jobs(
    user: CurrentUser,
    state: Annotated[VisualJobState | None, Query()] = None,
    cursor: Annotated[ResourceId | None, Query()] = None,
    include_total: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> VisualJobListResponse:
    return await _visual_queries.list_jobs(
        user_id=user.id,
        states=[state] if state else None,
        cursor=cursor,
        include_total=include_total,
        limit=limit,
    )


@router.get("/by-action/{action_id}", response_model=ActionMediaListResponse)
async def list_action_media(
    action_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)],
    user: CurrentUser,
    cursor: Annotated[ResourceId | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ActionMediaListResponse:
    """动作详情的媒体实现清单：该动作已制作的可播成品。"""

    return await _visual_queries.action_media_implementations(
        user_id=user.id, action_id=action_id, cursor=cursor, limit=limit
    )


@router.get("/presentation", response_model=VisualPresentationResponse)
async def get_presentation(user: CurrentUser) -> VisualPresentationResponse:
    return await _visuals.presentation_state(user_id=user.id)


@router.post(
    "/presentation/static-receipt",
    response_model=VisualReceiptResponse,
    openapi_extra=operation_policy(identities=("desktop",)),
)
async def static_receipt(user: CurrentUser, body: StageReceipt) -> VisualReceiptResponse:
    await record_static_receipt(user.id, body)
    return VisualReceiptResponse(ok=True)


@router.put("/presentation", response_model=VisualPresentationResponse)
async def put_presentation(user: CurrentUser, body: VisualPreferencesRequest) -> VisualPresentationResponse:
    return await _visuals.update_preferences(user_id=user.id, body=body)


@router.post(
    "/presentation/restore",
    response_model=VisualPlayCommand | None,
    openapi_extra=operation_policy(identities=("desktop",)),
)
async def restore_presentation(user: CurrentUser) -> VisualPlayCommand | None:
    return await _visuals.restore_presentation(user_id=user.id)


@router.get("/{visual_id}", response_model=VisualDetailView)
async def get_visual(
    visual_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser
) -> VisualDetailView:
    return await _visuals.visual_detail(user_id=user.id, visual_id=visual_id)


@router.post("/{visual_id}/regenerate", response_model=VisualJobView, status_code=202)
async def regenerate_visual(
    visual_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser, body: VisualRegenerateRequest
) -> VisualJobView:
    return await _visuals.regenerate(user_id=user.id, visual_id=visual_id, body=body)


@router.post("/jobs/{job_id}/commands", response_model=VisualJobView, status_code=202)
async def command_visual_job(
    job_id: Annotated[ResourceId, Path(gt=0, le=POSTGRES_INT8_MAX)], user: CurrentUser, body: VisualJobCommandRequest
) -> VisualJobView:
    return await _visuals.job_command(user_id=user.id, job_id=job_id, body=body)


@router.post(
    "/plays/{play_id}/claim",
    response_model=VisualClaimResponse,
    openapi_extra=operation_policy(identities=("desktop",)),
)
async def claim_play(
    play_id: Annotated[str, Path(min_length=8, max_length=36)], user: CurrentUser, body: VisualClaimRequest
) -> VisualClaimResponse:
    return await _visuals.claim_play(user_id=user.id, play_id=play_id, body=body)


@router.post(
    "/plays/{play_id}/receipt",
    response_model=VisualReceiptResponse,
    openapi_extra=operation_policy(identities=("desktop",)),
)
async def receipt_play(
    play_id: Annotated[str, Path(min_length=8, max_length=36)], user: CurrentUser, body: VisualReceiptRequest
) -> VisualReceiptResponse:
    return await _visuals.receipt_play(user_id=user.id, play_id=play_id, body=body)
