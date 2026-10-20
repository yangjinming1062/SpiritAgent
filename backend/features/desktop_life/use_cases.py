"""desktop_life 请求用例；拥有事务、响应装配与后台任务受理。"""

from pathlib import Path
from uuid import UUID

from infrastructure.config import SETTINGS
from infrastructure.constants import ATTACHMENT_VIDEO_EXTENSIONS, SCENE_DOWNLOAD_MAX_BYTES
from infrastructure.request_errors import RequestError
from infrastructure.uploads import UploadSource, read_capped_upload
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
    DesktopVideoCancelCommand,
    DesktopVideoClaimRequest,
    DesktopVideoConfirmImageCommand,
    DesktopVideoCreateSetRequest,
    DesktopVideoDecisionRequest,
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
    get_desktop_video_state,
    list_desktop_video_sets,
    pause_desktop_action,
    play_desktop_action,
    preview_desktop_video_refinement,
    record_desktop_playback,
    refine_desktop_video,
    rename_desktop_video_set,
    resume_desktop_action,
    retry_desktop_action,
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
        message=str(exc),
        code=exc.code,
        details=exc.details if isinstance(exc, DesktopVideoAspectRatioError) else None,
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
            target_width=body.target_width if body else None,
            target_height=body.target_height if body else None,
        )
    except DesktopVideoError as exc:
        raise _http_error(exc) from exc


async def command_draft(
    action_id: int,
    user_id: int,
    body: DesktopVideoGenerateCommand
    | DesktopVideoImageCommand
    | DesktopVideoRefineCommand
    | DesktopVideoConfirmImageCommand
    | DesktopVideoCancelCommand
    | DesktopVideoPauseCommand
    | DesktopVideoResumeCommand
    | DesktopVideoRetryCommand,
) -> DesktopVideoActionResponse:
    try:
        if isinstance(body, DesktopVideoGenerateCommand):
            return await generate_desktop_action(
                user_id,
                action_id,
                feedback=body.feedback,
                video_feedback=body.video_feedback,
                aspect_policy=body.aspect_policy,
                expected_draft_id=body.expected_draft_id,
                expected_base_version=body.expected_base_version,
                expected_resolution_revision=body.expected_resolution_revision,
                source="user_requested",
            )
        if isinstance(body, DesktopVideoImageCommand):
            return await generate_desktop_image(
                user_id,
                action_id,
                feedback=body.feedback,
                aspect_policy=body.aspect_policy,
                regenerate=body.regenerate,
                expected_draft_id=body.expected_draft_id,
                expected_base_version=body.expected_base_version,
                expected_resolution_revision=body.expected_resolution_revision,
            )
        if isinstance(body, DesktopVideoRefineCommand):
            return await refine_desktop_video(user_id, action_id, body)
        if isinstance(body, DesktopVideoConfirmImageCommand):
            return await confirm_desktop_image(
                user_id,
                action_id,
                draft_id=body.draft_id,
                expected_base_version=body.expected_base_version,
                expected_resolution_revision=body.expected_resolution_revision,
                generate_video=body.generate_video,
            )
        if isinstance(body, DesktopVideoCancelCommand):
            return await cancel_desktop_action(user_id, action_id, draft_id=body.draft_id)
        if isinstance(body, DesktopVideoPauseCommand):
            return await pause_desktop_action(user_id, action_id, draft_id=body.draft_id)
        if isinstance(body, DesktopVideoRetryCommand):
            return await retry_desktop_action(
                user_id,
                action_id,
                draft_id=body.draft_id,
                expected_base_version=body.expected_base_version,
                expected_resolution_revision=body.expected_resolution_revision,
            )
        return await resume_desktop_action(
            user_id,
            action_id,
            draft_id=body.draft_id,
            expected_base_version=body.expected_base_version,
            expected_resolution_revision=body.expected_resolution_revision,
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
            message="仅支持 MP4 或 MOV 视频",
            code="unsupported_video_format",
        )
    data = await read_capped_upload(
        file,
        SETTINGS.video_attachment_max_bytes,
        RequestError(status_code=413, message="视频文件超过大小上限", code="payload_too_large"),
    )
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


async def decide_action(
    action_id: int,
    user_id: int,
    body: DesktopVideoDecisionRequest,
) -> DesktopVideoActionResponse:
    try:
        return await review_desktop_action(
            user_id,
            action_id,
            accepted=body.decision == "accept",
            draft_id=body.draft_id,
            expected_base_version=body.expected_base_version,
            expected_resolution_revision=body.expected_resolution_revision,
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
    data = await read_capped_upload(
        file,
        SCENE_DOWNLOAD_MAX_BYTES,
        RequestError(status_code=413, message="图片为空或超过大小限制", code="invalid_image_size"),
    )
    if not data:
        raise RequestError(
            status_code=400,
            message="图片为空或超过大小限制",
            code="invalid_image_size",
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
