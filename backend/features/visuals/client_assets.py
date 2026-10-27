"""客户端交付时转换资产地址，持久化与内部快照继续保留裸路径。"""

from infrastructure.assets.asset_store import client_asset_url

from .schemas import ComboMaterialRef, SceneMediaRef, VisualPlayCommand


def client_material(material: ComboMaterialRef | None) -> ComboMaterialRef | None:
    if material is None:
        return None
    return material.model_copy(update={"path": client_asset_url(material.path)})


def client_scene(material: SceneMediaRef | None) -> SceneMediaRef | None:
    if material is None:
        return None
    return material.model_copy(update={"path": client_asset_url(material.path)})


def client_play_command(command: VisualPlayCommand) -> VisualPlayCommand:
    return command.model_copy(
        update={
            "media_path": client_asset_url(command.media_path),
            "poster_path": client_asset_url(command.poster_path) if command.poster_path else None,
            "outfit": client_material(command.outfit),
            "scene": client_scene(command.scene),
        }
    )
