"""客户端交付时转换资产地址，持久化与内部快照继续保留裸路径。"""

from infrastructure.assets.asset_store import client_asset_url

from .schemas import (
    ActorClipBody,
    AmbientCatalogEntry,
    ComboMaterialRef,
    SceneMediaRef,
    StageComposition,
    StageCompositionMedia,
    StageLayerMedia,
    VisualPlayCommand,
)


def client_material(material: ComboMaterialRef | None) -> ComboMaterialRef | None:
    if material is None:
        return None
    return material.model_copy(update={"path": client_asset_url(material.path)})


def client_scene(material: SceneMediaRef | None) -> SceneMediaRef | None:
    if material is None:
        return None
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


def client_layer_media(layer: StageLayerMedia | None) -> StageLayerMedia | None:
    if layer is None:
        return None
    return layer.model_copy(
        update={
            "media_url": client_asset_url(layer.media_url),
            "poster_url": client_asset_url(layer.poster_url) if layer.poster_url else None,
            "body": client_body(layer.body),
        }
    )


def client_catalog_entry(entry: AmbientCatalogEntry) -> AmbientCatalogEntry:
    return entry.model_copy(
        update={
            "media_url": client_asset_url(entry.media_url),
            "poster_url": client_asset_url(entry.poster_url) if entry.poster_url else None,
            "body": client_body(entry.body),
        }
    )


def client_composition(composition: StageComposition | None) -> StageComposition | None:
    if composition is None:
        return None
    media = composition.media
    return composition.model_copy(
        update={
            "outfit_material": client_material(composition.outfit_material),
            "scene_material": client_scene(composition.scene_material),
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
            "scene": client_scene(command.scene),
            "body": client_body(command.body),
            "composition": client_composition(command.composition),
        }
    )
