"""桌面生活视频：资源、制作、复核、播放与客户端回执。"""

from typing import Literal
from uuid import UUID

from fastapi import File, Form, HTTPException, UploadFile
from features.companion.schemas import CompanionOperationResponse
from features.desktop_life import use_cases as _desktop
from features.desktop_life.schemas import (
    DesktopVideoActionResponse,
    DesktopVideoCancelRequest,
    DesktopVideoClaimRequest,
    DesktopVideoConfirmImageRequest,
    DesktopVideoCreateSetRequest,
    DesktopVideoDesignRequest,
    DesktopVideoDraftRequest,
    DesktopVideoEnsureRequest,
    DesktopVideoExternalPromptRequest,
    DesktopVideoExternalPromptResponse,
    DesktopVideoGenerateRequest,
    DesktopVideoImageRequest,
    DesktopVideoListResponse,
    DesktopVideoPlayCommand,
    DesktopVideoPlayRequest,
    DesktopVideoPreferences,
    DesktopVideoProposalResponse,
    DesktopVideoReceipt,
    DesktopVideoRefinePreviewResponse,
    DesktopVideoRefineRequest,
    DesktopVideoRenameSetRequest,
    DesktopVideoSetResponse,
    DesktopVideoStateResponse,
)
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/desktop-videos", tag="companion-desktop-videos")


@router.get("/state", response_model=DesktopVideoStateResponse)
async def read_state(user: CurrentUser, db: DbSession) -> DesktopVideoStateResponse:
    try:
        return await _desktop.read_state(user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/sets", response_model=DesktopVideoListResponse)
async def read_sets(user: CurrentUser, db: DbSession) -> DesktopVideoListResponse:
    try:
        return await _desktop.read_sets(user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/actions/{action_id}", response_model=DesktopVideoActionResponse)
async def read_action(action_id: int, user: CurrentUser, db: DbSession) -> DesktopVideoActionResponse:
    try:
        return await _desktop.read_action(action_id=action_id, user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/ensure-current", response_model=DesktopVideoStateResponse, status_code=202)
async def prepare_current(
    user: CurrentUser,
    body: DesktopVideoEnsureRequest | None = None,
) -> DesktopVideoStateResponse:
    try:
        return await _desktop.prepare_current(user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/generate", response_model=DesktopVideoActionResponse, status_code=202)
async def generate_action(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoGenerateRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.generate_action(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/refine-video/preview", response_model=DesktopVideoRefinePreviewResponse)
async def preview_video_refinement(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoRefineRequest,
) -> DesktopVideoRefinePreviewResponse:
    try:
        return await _desktop.preview_video_refinement(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/refine-video", response_model=DesktopVideoActionResponse, status_code=202)
async def refine_video(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoRefineRequest,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.refine_video(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/external-prompt", response_model=DesktopVideoExternalPromptResponse)
async def external_prompt(
    action_id: int,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoExternalPromptRequest | None = None,
) -> DesktopVideoExternalPromptResponse:
    try:
        return await _desktop.external_prompt(action_id=action_id, user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/upload", response_model=DesktopVideoActionResponse, status_code=202)
async def upload_action(
    action_id: int,
    user: CurrentUser,
    file: UploadFile = File(...),
    expected_action_version: int = Form(..., ge=0),
    expected_set_id: int | None = Form(None),
    expected_draft_id: str | None = Form(None),
    expected_base_version: int | None = Form(None),
    expected_resolution_revision: int | None = Form(None),
    aspect_policy: Literal["ask", "keep", "expand"] = Form("ask"),
) -> DesktopVideoActionResponse:
    try:
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
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/accept", response_model=DesktopVideoActionResponse)
async def accept_action(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoDraftRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.accept_action(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/reject", response_model=DesktopVideoActionResponse)
async def reject_action(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoDraftRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.reject_action(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/design", response_model=DesktopVideoProposalResponse, status_code=202)
async def design_action(user: CurrentUser, body: DesktopVideoDesignRequest) -> DesktopVideoProposalResponse:
    try:
        return await _desktop.design_action(user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/play", response_model=DesktopVideoPlayCommand)
async def play_action(user: CurrentUser, db: DbSession, body: DesktopVideoPlayRequest) -> DesktopVideoPlayCommand:
    try:
        return await _desktop.play_action(user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.put("/preferences", response_model=DesktopVideoStateResponse)
async def update_preferences(
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoPreferences,
) -> DesktopVideoStateResponse:
    try:
        return await _desktop.update_preferences(user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/plays/{play_id}/claim")
async def claim_play(
    play_id: UUID,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoClaimRequest,
) -> dict[str, bool]:
    try:
        return await _desktop.claim_play(play_id=play_id, user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/plays/{play_id}/receipt", response_model=CompanionOperationResponse)
async def receipt_play(
    play_id: UUID,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoReceipt,
) -> CompanionOperationResponse:
    try:
        return await _desktop.receipt_play(play_id=play_id, user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/sets", response_model=DesktopVideoSetResponse, status_code=202)
async def create_set(user: CurrentUser, body: DesktopVideoCreateSetRequest) -> DesktopVideoSetResponse:
    try:
        return await _desktop.create_set(user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/sets/{set_id}", response_model=DesktopVideoSetResponse)
async def rename_set(
    set_id: int,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoRenameSetRequest,
) -> DesktopVideoSetResponse:
    try:
        return await _desktop.rename_set(set_id=set_id, user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/sets/{set_id}", response_model=DesktopVideoStateResponse)
async def delete_set(set_id: int, user: CurrentUser, db: DbSession) -> DesktopVideoStateResponse:
    try:
        return await _desktop.delete_set(set_id=set_id, user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/sets/{set_id}/activate", response_model=DesktopVideoStateResponse)
async def activate_set(set_id: int, user: CurrentUser, db: DbSession) -> DesktopVideoStateResponse:
    try:
        return await _desktop.activate_set(set_id=set_id, user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/image", response_model=DesktopVideoActionResponse, status_code=202)
async def generate_image(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoImageRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.generate_image(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/image-upload", response_model=DesktopVideoActionResponse)
async def upload_image(
    action_id: int,
    user: CurrentUser,
    file: UploadFile = File(...),
    expected_action_version: int = Form(..., ge=0),
    expected_set_id: int | None = Form(None),
    expected_draft_id: str | None = Form(None),
    expected_base_version: int | None = Form(None),
    expected_resolution_revision: int | None = Form(None),
    aspect_policy: Literal["ask", "keep", "expand"] = Form("ask"),
) -> DesktopVideoActionResponse:
    try:
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
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/confirm-image", response_model=DesktopVideoActionResponse, status_code=202)
async def confirm_image(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoConfirmImageRequest,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.confirm_image(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/cancel", response_model=DesktopVideoActionResponse)
async def cancel_action(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoCancelRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.cancel_action(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/pause", response_model=DesktopVideoActionResponse)
async def pause_action(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoDraftRequest,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.pause_action(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/actions/{action_id}/resume", response_model=DesktopVideoActionResponse)
async def resume_action(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoDraftRequest,
) -> DesktopVideoActionResponse:
    try:
        return await _desktop.resume_action(action_id=action_id, user_id=user.id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
