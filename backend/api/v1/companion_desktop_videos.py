"""桌面生活视频：资源、制作、复核、播放与客户端回执。"""

from pathlib import Path
from uuid import UUID

from common import get_router
from components import ATTACHMENT_VIDEO_EXTENSIONS, SETTINGS, DbSession
from fastapi import File, Form, HTTPException, UploadFile
from modules.auth import CurrentUser
from modules.companion import (
    CompanionOperationResponse,
    DesktopVideoActionResponse,
    DesktopVideoClaimRequest,
    DesktopVideoDesignRequest,
    DesktopVideoEnsureRequest,
    DesktopVideoExternalPromptRequest,
    DesktopVideoExternalPromptResponse,
    DesktopVideoGenerateRequest,
    DesktopVideoListResponse,
    DesktopVideoPlayCommand,
    DesktopVideoPlayRequest,
    DesktopVideoPreferences,
    DesktopVideoProposalResponse,
    DesktopVideoReceipt,
    DesktopVideoStateResponse,
)
from services.application.actions import (
    DesktopVideoError,
    DesktopVideoNotFoundError,
    DesktopVideoStateError,
    claim_desktop_playback,
    design_desktop_action,
    ensure_current_desktop_videos,
    generate_desktop_action,
    get_desktop_action_response,
    get_desktop_external_prompt,
    get_desktop_video_state,
    list_desktop_video_sets,
    play_desktop_action,
    record_desktop_playback,
    review_desktop_action,
    set_desktop_video_preferences,
    upload_desktop_action,
)

router = get_router(prefix="/api/companion/desktop-videos", tag="companion-desktop-videos")


def _http_error(exc: DesktopVideoError) -> HTTPException:
    status = (
        404 if isinstance(exc, DesktopVideoNotFoundError) else 409 if isinstance(exc, DesktopVideoStateError) else 400
    )
    return HTTPException(status_code=status, detail={"error": str(exc), "reason": str(exc)})


@router.get("/state", response_model=DesktopVideoStateResponse)
async def read_state(user: CurrentUser, db: DbSession) -> DesktopVideoStateResponse:
    return await get_desktop_video_state(db, user.id)


@router.get("/sets", response_model=DesktopVideoListResponse)
async def read_sets(user: CurrentUser, db: DbSession) -> DesktopVideoListResponse:
    return await list_desktop_video_sets(db, user.id)


@router.get("/actions/{action_id}", response_model=DesktopVideoActionResponse)
async def read_action(action_id: int, user: CurrentUser, db: DbSession) -> DesktopVideoActionResponse:
    try:
        return await get_desktop_action_response(db, user.id, action_id)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/ensure-current", response_model=DesktopVideoStateResponse, status_code=202)
async def prepare_current(
    user: CurrentUser,
    body: DesktopVideoEnsureRequest | None = None,
) -> DesktopVideoStateResponse:
    try:
        return await ensure_current_desktop_videos(
            user.id,
            source="autonomous" if body and body.trigger == "context_change" else "user_requested",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/actions/{action_id}/generate", response_model=DesktopVideoActionResponse, status_code=202)
async def generate_action(
    action_id: int,
    user: CurrentUser,
    body: DesktopVideoGenerateRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await generate_desktop_action(
            user.id,
            action_id,
            feedback=body.feedback if body else "",
            source="user_requested",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/actions/{action_id}/external-prompt",
    response_model=DesktopVideoExternalPromptResponse,
)
async def external_prompt(
    action_id: int,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoExternalPromptRequest | None = None,
) -> DesktopVideoExternalPromptResponse:
    try:
        return await get_desktop_external_prompt(
            db,
            user.id,
            action_id,
            expected_set_id=body.expected_set_id if body else None,
            requirements=body.requirements if body else "",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/actions/{action_id}/upload", response_model=DesktopVideoActionResponse, status_code=202)
async def upload_action(
    action_id: int,
    user: CurrentUser,
    file: UploadFile = File(...),
    expected_set_id: int | None = Form(None),
) -> DesktopVideoActionResponse:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ATTACHMENT_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={"error": "仅支持 MP4 或 MOV 视频", "reason": "unsupported_video_format"},
        )
    max_bytes = SETTINGS.video_attachment_max_bytes
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=413, detail={"error": "视频文件超过大小上限", "reason": "payload_too_large"})
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail={"error": "视频文件超过大小上限", "reason": "payload_too_large"})
    try:
        return await upload_desktop_action(
            user.id,
            action_id,
            data,
            extension,
            expected_set_id=expected_set_id,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/actions/{action_id}/accept", response_model=DesktopVideoActionResponse)
async def accept_action(action_id: int, user: CurrentUser) -> DesktopVideoActionResponse:
    try:
        return await review_desktop_action(user.id, action_id, accepted=True)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/actions/{action_id}/reject", response_model=DesktopVideoActionResponse)
async def reject_action(action_id: int, user: CurrentUser) -> DesktopVideoActionResponse:
    try:
        return await review_desktop_action(user.id, action_id, accepted=False)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/design", response_model=DesktopVideoProposalResponse, status_code=202)
async def design_action(user: CurrentUser, body: DesktopVideoDesignRequest) -> DesktopVideoProposalResponse:
    try:
        return await design_desktop_action(user.id, body, source="user_requested")
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/play", response_model=DesktopVideoPlayCommand)
async def play_action(user: CurrentUser, db: DbSession, body: DesktopVideoPlayRequest) -> DesktopVideoPlayCommand:
    try:
        command = await play_desktop_action(db, user.id, body, source="user_requested")
        await db.commit()
        return command
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.put("/preferences", response_model=DesktopVideoStateResponse)
async def update_preferences(
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoPreferences,
) -> DesktopVideoStateResponse:
    try:
        state = await set_desktop_video_preferences(db, user.id, body)
        await db.commit()
        return state
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/plays/{play_id}/claim")
async def claim_play(
    play_id: UUID,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoClaimRequest,
) -> dict[str, bool]:
    try:
        claimed = await claim_desktop_playback(db, user.id, str(play_id), body.client_id)
        await db.commit()
        return {"claimed": claimed}
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


@router.post("/plays/{play_id}/receipt", response_model=CompanionOperationResponse)
async def receipt_play(
    play_id: UUID,
    user: CurrentUser,
    db: DbSession,
    body: DesktopVideoReceipt,
) -> CompanionOperationResponse:
    try:
        await record_desktop_playback(db, user.id, str(play_id), body)
        await db.commit()
        return CompanionOperationResponse(ok=True)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc
