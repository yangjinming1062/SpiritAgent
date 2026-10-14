"""desktop_life 请求用例；拥有事务、响应装配与后台任务受理。"""

from pathlib import Path
from uuid import UUID

from infrastructure.config import SETTINGS
from infrastructure.constants import ATTACHMENT_VIDEO_EXTENSIONS
from infrastructure.request_errors import RequestError
from infrastructure.uploads import UploadSource
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.schemas import CompanionOperationResponse
from features.desktop_life.repository import DesktopVideoError, DesktopVideoNotFoundError, DesktopVideoStateError
from features.desktop_life.schemas import (
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
from features.desktop_life.service import (
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


def _http_error(exc: DesktopVideoError) -> RequestError:
    status = (
        404 if isinstance(exc, DesktopVideoNotFoundError) else 409 if isinstance(exc, DesktopVideoStateError) else 400
    )
    return RequestError(status_code=status, detail={"error": str(exc), "reason": str(exc)})


async def read_state(user_id: int, db: AsyncSession) -> DesktopVideoStateResponse:
    return await get_desktop_video_state(db, user_id)


async def read_sets(user_id: int, db: AsyncSession) -> DesktopVideoListResponse:
    return await list_desktop_video_sets(db, user_id)


async def read_action(action_id: int, user_id: int, db: AsyncSession) -> DesktopVideoActionResponse:
    try:
        return await get_desktop_action_response(db, user_id, action_id)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def prepare_current(user_id: int, body: DesktopVideoEnsureRequest | None = None) -> DesktopVideoStateResponse:
    try:
        return await ensure_current_desktop_videos(
            user_id,
            source="autonomous" if body and body.trigger == "context_change" else "user_requested",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def generate_action(
    action_id: int,
    user_id: int,
    body: DesktopVideoGenerateRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await generate_desktop_action(
            user_id,
            action_id,
            feedback=body.feedback if body else "",
            replace_base=body.replace_base if body else False,
            source="user_requested",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def external_prompt(
    action_id: int,
    user_id: int,
    db: AsyncSession,
    body: DesktopVideoExternalPromptRequest | None = None,
) -> DesktopVideoExternalPromptResponse:
    try:
        return await get_desktop_external_prompt(
            db,
            user_id,
            action_id,
            expected_set_id=body.expected_set_id if body else None,
            requirements=body.requirements if body else "",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def upload_action(
    action_id: int,
    user_id: int,
    file: UploadSource,
    expected_set_id: int | None = None,
) -> DesktopVideoActionResponse:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ATTACHMENT_VIDEO_EXTENSIONS:
        raise RequestError(
            status_code=415,
            detail={"error": "仅支持 MP4 或 MOV 视频", "reason": "unsupported_video_format"},
        )
    max_bytes = SETTINGS.video_attachment_max_bytes
    if file.size is not None and file.size > max_bytes:
        raise RequestError(status_code=413, detail={"error": "视频文件超过大小上限", "reason": "payload_too_large"})
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RequestError(status_code=413, detail={"error": "视频文件超过大小上限", "reason": "payload_too_large"})
    try:
        return await upload_desktop_action(user_id, action_id, data, extension, expected_set_id=expected_set_id)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def accept_action(action_id: int, user_id: int) -> DesktopVideoActionResponse:
    try:
        return await review_desktop_action(user_id, action_id, accepted=True)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def reject_action(action_id: int, user_id: int) -> DesktopVideoActionResponse:
    try:
        return await review_desktop_action(user_id, action_id, accepted=False)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def design_action(user_id: int, body: DesktopVideoDesignRequest) -> DesktopVideoProposalResponse:
    try:
        return await design_desktop_action(user_id, body, source="user_requested")
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def play_action(user_id: int, db: AsyncSession, body: DesktopVideoPlayRequest) -> DesktopVideoPlayCommand:
    try:
        command = await play_desktop_action(db, user_id, body, source="user_requested")
        await db.commit()
        return command
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def update_preferences(
    user_id: int,
    db: AsyncSession,
    body: DesktopVideoPreferences,
) -> DesktopVideoStateResponse:
    try:
        state = await set_desktop_video_preferences(db, user_id, body)
        await db.commit()
        return state
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def claim_play(play_id: UUID, user_id: int, db: AsyncSession, body: DesktopVideoClaimRequest) -> dict[str, bool]:
    try:
        claimed = await claim_desktop_playback(db, user_id, str(play_id), body.client_id)
        await db.commit()
        return {"claimed": claimed}
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def receipt_play(
    play_id: UUID,
    user_id: int,
    db: AsyncSession,
    body: DesktopVideoReceipt,
) -> CompanionOperationResponse:
    try:
        await record_desktop_playback(db, user_id, str(play_id), body)
        await db.commit()
        return CompanionOperationResponse(ok=True)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc
