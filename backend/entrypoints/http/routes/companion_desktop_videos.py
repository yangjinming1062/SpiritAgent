"""桌面生活视频：资源、制作、复核、播放与客户端回执。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Body, File, Form, Path, UploadFile
from features.companion.schemas import CompanionOperationResponse
from features.desktop_life import use_cases as _desktop
from features.desktop_life.schemas import (
    DesktopVideoActionResponse,
    DesktopVideoCancelCommand,
    DesktopVideoClaimRequest,
    DesktopVideoCreateSetRequest,
    DesktopVideoDesignRequest,
    DesktopVideoEnsureRequest,
    DesktopVideoGenerateCommand,
    DesktopVideoImageCommand,
    DesktopVideoListResponse,
    DesktopVideoPauseCommand,
    DesktopVideoPlayCommand,
    DesktopVideoPlayRequest,
    DesktopVideoPreferences,
    DesktopVideoProposalResponse,
    DesktopVideoReceipt,
    DesktopVideoRefineCommand,
    DesktopVideoRefinePreviewResponse,
    DesktopVideoRefineRequest,
    DesktopVideoRenameSetRequest,
    DesktopVideoResumeCommand,
    DesktopVideoRetryCommand,
    DesktopVideoSelectionRequest,
    DesktopVideoSetResponse,
    DesktopVideoStateResponse,
)
from infrastructure.constants import POSTGRES_INT4_MAX
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/desktop-videos", tag="companion-desktop-videos")


@router.get("/state", response_model=DesktopVideoStateResponse)
async def read_state(user: CurrentUser, db: DbSession) -> DesktopVideoStateResponse:
    return await _desktop.read_state(user_id=user.id, db=db)


@router.get("/sets", response_model=DesktopVideoListResponse)
async def read_sets(user: CurrentUser, db: DbSession) -> DesktopVideoListResponse:
    return await _desktop.read_sets(user_id=user.id, db=db)


@router.get(
    "/actions/{action_id}",
    response_model=DesktopVideoActionResponse,
)
async def read_action(
    action_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
) -> DesktopVideoActionResponse:
    return await _desktop.read_action(action_id=action_id, user_id=user.id, db=db)


@router.post(
    "/ensure-current",
    response_model=DesktopVideoStateResponse,
    status_code=202,
)
async def prepare_current(
    user: CurrentUser,
    body: DesktopVideoEnsureRequest | None = None,
) -> DesktopVideoStateResponse:
    return await _desktop.prepare_current(user_id=user.id, body=body)


@router.post(
    "/actions/{action_id}/refine-video/preview",
    response_model=DesktopVideoRefinePreviewResponse,
)
async def preview_video_refinement(
    action_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    body: DesktopVideoRefineRequest,
) -> DesktopVideoRefinePreviewResponse:
    return await _desktop.preview_video_refinement(action_id=action_id, user_id=user.id, body=body)


@router.post(
    "/actions/{action_id}/upload",
    response_model=DesktopVideoActionResponse,
    status_code=202,
    openapi_extra=operation_policy(upload="video"),
)
async def upload_action(
    action_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    file: UploadFile = File(...),
    expected_action_version: int = Form(..., ge=0),
    expected_set_id: int | None = Form(None, gt=0, le=POSTGRES_INT4_MAX),
    expected_draft_id: str | None = Form(None),
    expected_base_version: int | None = Form(None),
    expected_resolution_revision: int | None = Form(None),
    aspect_policy: Literal["ask", "keep", "expand"] = Form("ask"),
) -> DesktopVideoActionResponse:
    return await _desktop.upload_action(
        action_id=action_id,
        user_id=user.id,
        file=file,
        expected_action_version=expected_action_version,
        expected_set_id=expected_set_id,
        expected_draft_id=expected_draft_id,
        expected_base_version=expected_base_version,
        expected_resolution_revision=expected_resolution_revision,
        aspect_policy=aspect_policy,
    )


@router.post(
    "/design",
    response_model=DesktopVideoProposalResponse,
    status_code=202,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def design_action(user: CurrentUser, body: DesktopVideoDesignRequest) -> DesktopVideoProposalResponse:
    return await _desktop.design_action(user_id=user.id, body=body)


@router.post("/play", response_model=DesktopVideoPlayCommand)
async def play_action(user: CurrentUser, db: DbSession, body: DesktopVideoPlayRequest) -> DesktopVideoPlayCommand:
    return await _desktop.play_action(user_id=user.id, db=db, body=body)


@router.put(
    "/preferences",
    response_model=DesktopVideoStateResponse,
)
async def update_preferences(
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoPreferences,
) -> DesktopVideoStateResponse:
    return await _desktop.update_preferences(user_id=user.id, db=db, body=body)


@router.post("/plays/{play_id}/claim")
async def claim_play(
    play_id: UUID,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoClaimRequest,
) -> dict[str, bool]:
    return await _desktop.claim_play(play_id=play_id, user_id=user.id, db=db, body=body)


@router.post(
    "/plays/{play_id}/receipt",
    response_model=CompanionOperationResponse,
)
async def receipt_play(
    play_id: UUID,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoReceipt,
) -> CompanionOperationResponse:
    return await _desktop.receipt_play(play_id=play_id, user_id=user.id, db=db, body=body)


@router.post(
    "/sets",
    response_model=DesktopVideoSetResponse,
    status_code=202,
)
async def create_set(user: CurrentUser, body: DesktopVideoCreateSetRequest) -> DesktopVideoSetResponse:
    return await _desktop.create_set(user_id=user.id, body=body)


@router.patch(
    "/sets/{set_id}",
    response_model=DesktopVideoSetResponse,
)
async def rename_set(
    set_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoRenameSetRequest,
) -> DesktopVideoSetResponse:
    return await _desktop.rename_set(set_id=set_id, user_id=user.id, db=db, body=body)


@router.delete(
    "/sets/{set_id}",
    response_model=DesktopVideoStateResponse,
)
async def delete_set(
    set_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    db: DbSession,
) -> DesktopVideoStateResponse:
    return await _desktop.delete_set(set_id=set_id, user_id=user.id, db=db)


@router.post(
    "/actions/{action_id}/image-upload",
    response_model=DesktopVideoActionResponse,
    openapi_extra=operation_policy(upload="image_file"),
)
async def upload_image(
    action_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    file: UploadFile = File(...),
    expected_action_version: int = Form(..., ge=0),
    expected_set_id: int | None = Form(None, gt=0, le=POSTGRES_INT4_MAX),
    expected_draft_id: str | None = Form(None),
    expected_base_version: int | None = Form(None),
    expected_resolution_revision: int | None = Form(None),
    aspect_policy: Literal["ask", "keep", "expand"] = Form("ask"),
) -> DesktopVideoActionResponse:
    return await _desktop.upload_image(
        action_id=action_id,
        user_id=user.id,
        file=file,
        expected_action_version=expected_action_version,
        expected_set_id=expected_set_id,
        expected_draft_id=expected_draft_id,
        expected_base_version=expected_base_version,
        expected_resolution_revision=expected_resolution_revision,
        aspect_policy=aspect_policy,
    )


@router.post(
    "/actions/{action_id}/draft/commands",
    response_model=DesktopVideoActionResponse,
    status_code=202,
)
async def command_draft(
    action_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    user: CurrentUser,
    body: Annotated[
        DesktopVideoGenerateCommand
        | DesktopVideoImageCommand
        | DesktopVideoRefineCommand
        | DesktopVideoCancelCommand
        | DesktopVideoPauseCommand
        | DesktopVideoResumeCommand
        | DesktopVideoRetryCommand,
        Body(discriminator="command"),
    ],
) -> DesktopVideoActionResponse:
    return await _desktop.command_draft(action_id=action_id, user_id=user.id, body=body)


@router.put(
    "/current",
    response_model=DesktopVideoStateResponse,
)
async def select_current_set(
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoSelectionRequest,
) -> DesktopVideoStateResponse:
    return await _desktop.activate_set(set_id=body.set_id, user_id=user.id, db=db)
