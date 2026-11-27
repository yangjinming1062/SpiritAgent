"""影像库请求用例；只做参数收敛与错误转换，业务语义在 service 与 queries。"""

from typing import Literal

from infrastructure.assets.asset_store import client_asset_url

from . import queries, service
from .client_assets import (
    client_composition,
    client_layer_media,
    client_material,
    client_play_command,
    client_stage_plan,
)
from .schemas import (
    LocalVisualReceipt,
    PlaybackSource,
    VisualClaimRequest,
    VisualClaimResponse,
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


async def _presentation_response(user_id: int, presentation: VisualPresentationView) -> VisualPresentationResponse:
    actual = await queries.actual_presentation_snapshot(user_id)
    wallpaper = presentation.default_wallpaper
    static = presentation.static_presentation
    presentation = presentation.model_copy(
        update={
            "ambient_catalog": [client_layer_media(entry) for entry in presentation.ambient_catalog],
            "stage_plan": client_stage_plan(presentation.stage_plan),
            "pending_play": client_play_command(presentation.pending_play) if presentation.pending_play else None,
            "composition": client_composition(presentation.composition),
            "default_wallpaper": wallpaper.model_copy(update={"composition": client_composition(wallpaper.composition)})
            if wallpaper
            else None,
            "static_presentation": static.model_copy(
                update={
                    "outfit": client_material(static.outfit),
                    "scene": client_material(static.scene),
                    "identity_path": client_asset_url(static.identity_path) if static.identity_path else None,
                }
            )
            if static
            else None,
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


async def job_command(user_id: int, job_id: int, body: VisualJobCommandRequest) -> VisualJobView:
    if body.command == "pause":
        return await service.pause_job(user_id, job_id)
    if body.command == "resume":
        return await service.resume_job(user_id, job_id, user_requested=True)
    if body.command == "cancel":
        return await service.cancel_job(user_id, job_id)
    return await service.retry_job(user_id, job_id, user_requested=True)


async def regenerate(user_id: int, visual_id: int, body: VisualRegenerateRequest) -> VisualJobView:
    return await service.regenerate_visual(
        user_id,
        visual_id,
        expected_version=body.expected_version,
        feedback=body.feedback,
        video_feedback=body.video_feedback,
    )


async def claim_play(user_id: int, play_id: str, body: VisualClaimRequest) -> VisualClaimResponse:
    claimed = await service.claim_playback(
        user_id, play_id, client_id=body.client_id, target_surface=body.target_surface
    )
    return VisualClaimResponse(claimed=claimed)


async def receipt_play(user_id: int, play_id: str, body: VisualReceiptRequest) -> VisualReceiptResponse:
    await service.record_playback(
        user_id,
        play_id,
        status=body.status,
        receipt_kind=body.receipt_kind,
        client_id=body.client_id,
        error=body.error,
    )
    return VisualReceiptResponse(ok=True)


async def local_receipt(user_id: int, body: LocalVisualReceipt) -> VisualReceiptResponse:
    await service.record_local_playback(user_id, body)
    return VisualReceiptResponse(ok=True)


async def presentation_state(user_id: int) -> VisualPresentationResponse:
    return await _presentation_response(user_id, await queries.presentation_state(user_id))


async def restore_presentation(user_id: int) -> VisualPlayCommand | None:
    command = await service.restore_presentation(user_id)
    return client_play_command(command) if command is not None else None


async def play_visual(
    user_id: int,
    visual_id: int,
    version: int,
    origin: Literal["user", "life"] = "user",
    life_revision: int | None = None,
) -> VisualPlayCommand:
    source: PlaybackSource = "life" if origin == "life" else "manual"
    command = await service.play_visual(
        user_id,
        visual_id,
        source=source,
        origin=origin,
        version=version,
        expected_life_revision=life_revision if origin == "life" else None,
    )
    return client_play_command(command)


async def update_preferences(user_id: int, body: VisualPreferencesRequest) -> VisualPresentationResponse:
    presentation = await service.update_presentation_preferences(
        user_id,
        autonomous_enabled=body.autonomous_enabled,
        default_visual_id=body.default_visual_id,
        set_default_visual="default_visual_id" in body.model_fields_set,
        target_width=body.target_width,
        target_height=body.target_height,
    )
    return await _presentation_response(user_id, presentation)
