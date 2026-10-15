"""desktop_life 请求用例；拥有事务、响应装配与后台任务受理。"""

from pathlib import Path
from uuid import UUID

from infrastructure.config import SETTINGS
from infrastructure.constants import ATTACHMENT_VIDEO_EXTENSIONS, SCENE_DOWNLOAD_MAX_BYTES
from infrastructure.request_errors import RequestError
from infrastructure.uploads import UploadSource
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.schemas import CompanionOperationResponse
from features.desktop_life.repository import (
    DesktopVideoAspectRatioError,
    DesktopVideoError,
    DesktopVideoNotFoundError,
    DesktopVideoStateError,
)
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
from features.desktop_life.service import (
    activate_desktop_video_set,
    cancel_desktop_action,
    claim_desktop_playback,
    confirm_desktop_image,
    create_desktop_video_set,
    delete_desktop_video_set,
    design_desktop_action,
    ensure_current_desktop_videos,
    generate_desktop_action,
    generate_desktop_image,
    get_desktop_action_response,
    get_desktop_external_prompt,
    get_desktop_video_state,
    list_desktop_video_sets,
    pause_desktop_action,
    play_desktop_action,
    preview_desktop_video_refinement,
    record_desktop_playback,
    refine_desktop_video,
    rename_desktop_video_set,
    resume_desktop_action,
    review_desktop_action,
    set_desktop_video_preferences,
    upload_desktop_action,
    upload_desktop_image,
)


def _http_error(exc: DesktopVideoError) -> RequestError:
    status = (
        404 if isinstance(exc, DesktopVideoNotFoundError) else 409 if isinstance(exc, DesktopVideoStateError) else 400
    )
    return RequestError(
        status_code=status,
        detail=exc.detail if isinstance(exc, DesktopVideoAspectRatioError) else {"error": str(exc), "reason": str(exc)},
    )


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
            source="user_requested",
            target_width=body.target_width if body else None,
            target_height=body.target_height if body else None,
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
            aspect_policy=body.aspect_policy if body else "ask",
            expected_draft_id=body.expected_draft_id if body else None,
            expected_base_version=body.expected_base_version if body else None,
            expected_resolution_revision=body.expected_resolution_revision if body else None,
            source="user_requested",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def preview_video_refinement(
    action_id: int,
    user_id: int,
    body: DesktopVideoRefineRequest,
) -> DesktopVideoRefinePreviewResponse:
    try:
        return await preview_desktop_video_refinement(user_id, action_id, body)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def refine_video(
    action_id: int,
    user_id: int,
    body: DesktopVideoRefineRequest,
) -> DesktopVideoActionResponse:
    try:
        return await refine_desktop_video(user_id, action_id, body)
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
            expected_draft_id=body.expected_draft_id if body else None,
            expected_base_version=body.expected_base_version if body else None,
            expected_resolution_revision=body.expected_resolution_revision if body else None,
            aspect_policy=body.aspect_policy if body else "ask",
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def upload_action(
    action_id: int,
    user_id: int,
    file: UploadSource,
    expected_action_version: int,
    expected_set_id: int | None = None,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    aspect_policy: str = "ask",
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
        return await upload_desktop_action(
            user_id,
            action_id,
            data,
            extension,
            expected_action_version=expected_action_version,
            expected_set_id=expected_set_id,
            expected_draft_id=expected_draft_id or "none",
            expected_base_version=expected_base_version,
            expected_resolution_revision=expected_resolution_revision,
            aspect_policy=aspect_policy,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def accept_action(
    action_id: int,
    user_id: int,
    body: DesktopVideoDraftRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await review_desktop_action(
            user_id,
            action_id,
            accepted=True,
            draft_id=body.draft_id if body else None,
            expected_base_version=body.expected_base_version if body else None,
            expected_resolution_revision=body.expected_resolution_revision if body else None,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def reject_action(
    action_id: int,
    user_id: int,
    body: DesktopVideoDraftRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await review_desktop_action(
            user_id,
            action_id,
            accepted=False,
            draft_id=body.draft_id if body else None,
            expected_base_version=body.expected_base_version if body else None,
            expected_resolution_revision=body.expected_resolution_revision if body else None,
        )
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


async def create_set(user_id: int, body: DesktopVideoCreateSetRequest) -> DesktopVideoSetResponse:
    try:
        return await create_desktop_video_set(user_id, body)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def rename_set(
    set_id: int,
    user_id: int,
    db: AsyncSession,
    body: DesktopVideoRenameSetRequest,
) -> DesktopVideoSetResponse:
    try:
        return await rename_desktop_video_set(db, user_id, set_id, body)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def delete_set(set_id: int, user_id: int, db: AsyncSession) -> DesktopVideoStateResponse:
    try:
        return await delete_desktop_video_set(db, user_id, set_id)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def activate_set(set_id: int, user_id: int, db: AsyncSession) -> DesktopVideoStateResponse:
    try:
        return await activate_desktop_video_set(db, user_id, set_id)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def generate_image(
    action_id: int,
    user_id: int,
    body: DesktopVideoImageRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await generate_desktop_image(
            user_id,
            action_id,
            feedback=body.feedback if body else "",
            aspect_policy=body.aspect_policy if body else "ask",
            regenerate=body.regenerate if body else False,
            expected_draft_id=body.expected_draft_id if body else None,
            expected_base_version=body.expected_base_version if body else None,
            expected_resolution_revision=body.expected_resolution_revision if body else None,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def upload_image(
    action_id: int,
    user_id: int,
    file: UploadSource,
    expected_action_version: int,
    expected_set_id: int | None = None,
    expected_draft_id: str | None = None,
    expected_base_version: int | None = None,
    expected_resolution_revision: int | None = None,
    aspect_policy: str = "ask",
) -> DesktopVideoActionResponse:
    max_bytes = SCENE_DOWNLOAD_MAX_BYTES
    data = await file.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        raise RequestError(
            status_code=413 if data else 400,
            detail={"error": "图片为空或超过大小限制", "reason": "invalid_image_size"},
        )
    try:
        return await upload_desktop_image(
            user_id,
            action_id,
            data,
            expected_action_version=expected_action_version,
            expected_set_id=expected_set_id,
            expected_draft_id=expected_draft_id or "none",
            expected_base_version=expected_base_version,
            expected_resolution_revision=expected_resolution_revision,
            aspect_policy=aspect_policy,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def confirm_image(
    action_id: int,
    user_id: int,
    body: DesktopVideoConfirmImageRequest,
) -> DesktopVideoActionResponse:
    try:
        return await confirm_desktop_image(
            user_id,
            action_id,
            draft_id=body.draft_id,
            expected_base_version=body.expected_base_version,
            expected_resolution_revision=body.expected_resolution_revision,
            generate_video=body.generate_video,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def cancel_action(
    action_id: int,
    user_id: int,
    body: DesktopVideoCancelRequest | None = None,
) -> DesktopVideoActionResponse:
    try:
        return await cancel_desktop_action(user_id, action_id, draft_id=body.draft_id if body else None)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def pause_action(
    action_id: int,
    user_id: int,
    body: DesktopVideoDraftRequest,
) -> DesktopVideoActionResponse:
    try:
        return await pause_desktop_action(user_id, action_id, draft_id=body.draft_id)
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def resume_action(
    action_id: int,
    user_id: int,
    body: DesktopVideoDraftRequest,
) -> DesktopVideoActionResponse:
    try:
        return await resume_desktop_action(
            user_id,
            action_id,
            draft_id=body.draft_id,
            expected_base_version=body.expected_base_version,
            expected_resolution_revision=body.expected_resolution_revision,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc
