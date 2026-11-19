"""客户端交付时转换资产地址，持久化与内部快照继续保留裸路径。"""

from infrastructure.assets.asset_store import client_asset_url

from .schemas import (
    ActorClipBody,
    ComboMaterialRef,
    SceneMediaRef,
    StageComposition,
    StageCompositionMedia,
    StageLayerMedia,
    StagePlaybackPlan,
    VisualPlayCommand,
)


def client_material[T: ComboMaterialRef | SceneMediaRef | None](material: T) -> T:
    if material is None:
        return material
    return material.model_copy(update={"path": client_asset_url(material.path)})


def client_body(body: ActorClipBody | None) -> ActorClipBody | None:
    if body is None:
        return None
    return body.model_copy(
        update={
            "mask_path": client_asset_url(body.mask_path) if body.mask_path else None,
            "first_frame_path": client_asset_url(body.first_frame_path) if body.first_frame_path else None,
            "last_frame_path": client_asset_url(body.last_frame_path) if body.last_frame_path else None,
        }
    )


def client_layer_media[T: StageLayerMedia | None](layer: T) -> T:
    if layer is None:
        return layer
    return layer.model_copy(
        update={
            "media_url": client_asset_url(layer.media_url),
            "poster_url": client_asset_url(layer.poster_url) if layer.poster_url else None,
            "body": client_body(layer.body),
        }
    )


def client_stage_plan(plan: StagePlaybackPlan | None) -> StagePlaybackPlan | None:
    if plan is None:
        return None
    return plan.model_copy(
        update={
            "scene": client_material(plan.scene),
            "scene_media": client_layer_media(plan.scene_media),
            "actor_variants": [client_layer_media(entry) for entry in plan.actor_variants],
            "fallback_actor_variants": [client_layer_media(entry) for entry in plan.fallback_actor_variants],
            "cinematic_variants": [client_layer_media(entry) for entry in plan.cinematic_variants],
            "actor_still_url": client_asset_url(plan.actor_still_url) if plan.actor_still_url else None,
            "wake_actor": client_layer_media(plan.wake_actor),
            "wake_scene": client_material(plan.wake_scene),
            "wake_scene_media": client_layer_media(plan.wake_scene_media),
        }
    )


def client_composition(composition: StageComposition | None) -> StageComposition | None:
    if composition is None:
        return None
    media = composition.media
    return composition.model_copy(
        update={
            "outfit_material": client_material(composition.outfit_material),
            "scene_material": client_material(composition.scene_material),
            "identity_path": client_asset_url(composition.identity_path) if composition.identity_path else None,
            "media": StageCompositionMedia(
                mode=media.mode,
                scene=client_layer_media(media.scene),
                actor=client_layer_media(media.actor),
                cinematic=client_layer_media(media.cinematic),
            )
            if media
            else None,
        }
    )


def client_play_command(command: VisualPlayCommand) -> VisualPlayCommand:
    return command.model_copy(
        update={
            "media_path": client_asset_url(command.media_path),
            "poster_path": client_asset_url(command.poster_path) if command.poster_path else None,
            "outfit": client_material(command.outfit),
            "scene": client_material(command.scene),
            "body": client_body(command.body),
            "composition": client_composition(command.composition),
        }
    )
