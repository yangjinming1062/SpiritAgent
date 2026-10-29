"""影像库请求用例；只做参数收敛与错误转换，业务语义在 service 与 queries。"""

from infrastructure.assets.asset_store import client_asset_url
from infrastructure.request_errors import RequestError

from . import queries, service
from .client_assets import (
    client_catalog_entry,
    client_composition,
    client_layer_media,
    client_material,
    client_play_command,
    client_scene,
)
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
    actual = await queries.actual_presentation_snapshot(user_id)
    presentation = presentation.model_copy(update={"ambient_actor": client_layer_media(presentation.ambient_actor)})
    presentation = presentation.model_copy(
        update={"ambient_catalog": [client_catalog_entry(entry) for entry in presentation.ambient_catalog]}
    )
    if presentation.pending_play is not None:
        presentation = presentation.model_copy(update={"pending_play": client_play_command(presentation.pending_play)})
    if presentation.composition is not None:
        presentation = presentation.model_copy(update={"composition": client_composition(presentation.composition)})
    if presentation.static_presentation is not None:
        static = presentation.static_presentation
        presentation = presentation.model_copy(
            update={
                "static_presentation": static.model_copy(
                    update={
                        "outfit": client_material(static.outfit) if static.outfit else None,
                        "scene": client_scene(static.scene),
                        "identity_path": client_asset_url(static.identity_path) if static.identity_path else None,
                    }
                )
            }
        )
    return VisualPresentationResponse(
        **presentation.model_dump(),
        actual=actual.model_copy(
            update={
                "visual_path": client_asset_url(actual.visual_path) if actual.visual_path else None,
                "outfit_path": client_asset_url(actual.outfit_path) if actual.outfit_path else None,
                "scene_path": client_asset_url(actual.scene_path) if actual.scene_path else None,
                "identity_path": client_asset_url(actual.identity_path) if actual.identity_path else None,
            }
        ),
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
        command = await service.restore_presentation(user_id)
        return client_play_command(command) if command is not None else None
    except VisualError as exc:
        raise _http_error(exc) from exc


async def play_visual(user_id: int, visual_id: int, version: int) -> VisualPlayCommand:
    try:
        command = await service.play_visual(user_id, visual_id, source="remote", version=version)
    except VisualError as exc:
        raise _http_error(exc) from exc
    return client_play_command(command)


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
