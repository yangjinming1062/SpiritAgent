"""影像库请求用例；只做参数收敛与错误转换，业务语义在 service 与 queries。"""

from infrastructure.assets.asset_store import client_asset_url
from infrastructure.request_errors import RequestError

from . import queries, service
from .repository import VisualError, VisualNotFoundError, VisualStateError
from .schemas import (
    VisualClaimRequest,
    VisualClaimResponse,
    VisualDetailView,
    VisualJobCommandRequest,
    VisualJobView,
    VisualPlayCommand,
    VisualPreferencesRequest,
    VisualPresentationResponse,
    VisualPresentationView,
    VisualReceiptRequest,
    VisualReceiptResponse,
    VisualRegenerateRequest,
)


def _http_error(exc: VisualError) -> RequestError:
    status = 404 if isinstance(exc, VisualNotFoundError) else 409 if isinstance(exc, VisualStateError) else 400
    return RequestError(status_code=status, message=str(exc))


async def _presentation_response(user_id: int, presentation: VisualPresentationView) -> VisualPresentationResponse:
    if presentation.static_presentation is not None:
        static = presentation.static_presentation
        presentation = presentation.model_copy(
            update={
                "static_presentation": static.model_copy(
                    update={
                        "outfit": static.outfit.model_copy(update={"path": client_asset_url(static.outfit.path)})
                        if static.outfit
                        else None,
                        "scene": static.scene.model_copy(update={"path": client_asset_url(static.scene.path)})
                        if static.scene
                        else None,
                        "identity_path": client_asset_url(static.identity_path) if static.identity_path else None,
                    }
                )
            }
        )
    return VisualPresentationResponse(
        **presentation.model_dump(),
        actual=await queries.actual_presentation_snapshot(user_id),
    )


async def visual_detail(user_id: int, visual_id: int) -> VisualDetailView:
    try:
        return await queries.visual_detail(user_id, visual_id)
    except VisualError as exc:
        raise _http_error(exc) from exc


async def job_command(user_id: int, job_id: int, body: VisualJobCommandRequest) -> VisualJobView:
    try:
        if body.command == "pause":
            return await service.pause_job(user_id, job_id)
        if body.command == "resume":
            return await service.resume_job(user_id, job_id)
        if body.command == "cancel":
            return await service.cancel_job(user_id, job_id)
        return await service.retry_job(user_id, job_id)
    except VisualError as exc:
        raise _http_error(exc) from exc


async def regenerate(user_id: int, visual_id: int, body: VisualRegenerateRequest) -> VisualJobView:
    try:
        return await service.regenerate_visual(
            user_id,
            visual_id,
            expected_version=body.expected_version,
            feedback=body.feedback,
            video_feedback=body.video_feedback,
        )
    except VisualError as exc:
        raise _http_error(exc) from exc


async def claim_play(user_id: int, play_id: str, body: VisualClaimRequest) -> VisualClaimResponse:
    try:
        claimed = await service.claim_playback(user_id, play_id, client_id=body.client_id)
    except VisualError as exc:
        raise _http_error(exc) from exc
    return VisualClaimResponse(claimed=claimed)


async def receipt_play(user_id: int, play_id: str, body: VisualReceiptRequest) -> VisualReceiptResponse:
    try:
        await service.record_playback(
            user_id,
            play_id,
            status=body.status,
            receipt_kind=body.receipt_kind,
            client_id=body.client_id,
            error=body.error,
        )
    except VisualError as exc:
        raise _http_error(exc) from exc
    return VisualReceiptResponse(ok=True)


async def presentation_state(user_id: int) -> VisualPresentationResponse:
    return await _presentation_response(user_id, await queries.presentation_state(user_id))


async def restore_presentation(user_id: int) -> VisualPlayCommand | None:
    try:
        return await service.restore_presentation(user_id)
    except VisualError as exc:
        raise _http_error(exc) from exc


async def update_preferences(user_id: int, body: VisualPreferencesRequest) -> VisualPresentationResponse:
    try:
        presentation = await service.update_presentation_preferences(
            user_id,
            autonomous_enabled=body.autonomous_enabled,
            target_width=body.target_width,
            target_height=body.target_height,
        )
    except VisualError as exc:
        raise _http_error(exc) from exc
    return await _presentation_response(user_id, presentation)
