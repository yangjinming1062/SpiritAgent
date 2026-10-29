"""成品的自动使用资格与文件完整性；不判断生活情境或调度时机。"""

from infrastructure.assets.asset_store import resolve_asset_reference


def existing_asset(reference: str | None) -> str | None:
    resolved = resolve_asset_reference(reference)
    return reference if resolved is not None and resolved[0].is_file() else None


def actor_body_ready(body: dict | None, clip_kind: str | None) -> bool:
    return bool(
        body
        and (clip_kind == "once" or body.get("continuity_key"))
        and all(existing_asset(body.get(field)) for field in ("mask_path", "first_frame_path", "last_frame_path"))
    )


def automatic_media_ready(
    *,
    media_kind: str,
    asset_kind: str,
    media_path: str,
    auto_selectable: bool | None,
    clip_kind: str | None,
    body: dict | None,
) -> bool:
    return bool(
        media_kind == "video"
        and (asset_kind == "scene" or (asset_kind in ("actor", "cinematic") and auto_selectable is True))
        and existing_asset(media_path)
        and (asset_kind != "actor" or actor_body_ready(body, clip_kind))
    )
