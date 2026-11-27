"""按来源经历记录的成果身份冻结可分享的原件。"""

from features.appearance.queries import ready_outfit_snapshot
from features.life.episode_media import EpisodeOriginalAsset
from features.scenes.queries import scene_snapshot
from features.visuals.repository import presented_version
from infrastructure.assets.asset_store import normalize_asset_reference
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.library_visibility import released_work_file
from sqlalchemy.ext.asyncio import AsyncSession


async def _public_path(db: AsyncSession, user_id: int, value: str | None) -> str | None:
    path = normalize_asset_reference(value)
    parsed = parse_companion_asset_path(path)
    if parsed is None or parsed[0] != user_id:
        return None
    scope, _, rest = parsed[1].partition("/")
    if scope.casefold() == "private-work":
        work_id, _, _ = rest.partition("/")
        if not work_id.isdecimal() or not await released_work_file(db, user_id, int(work_id), path):
            return None
    return path


async def resolve_shared_original(
    db: AsyncSession, user_id: int, result: dict[str, int]
) -> EpisodeOriginalAsset | None:
    if version_id := result.get("visual_version_id"):
        version = await presented_version(db, user_id, version_id)
        if version is not None and version.id == result.get("visual_id") and version.status == "ready":
            path = await _public_path(db, user_id, version.media_path)
            if path is not None:
                poster = await _public_path(db, user_id, version.poster_path) if version.media_kind == "video" else None
                return EpisodeOriginalAsset(version.media_kind, path, poster)
        return None
    if scene_id := result.get("scene_id"):
        scene = await scene_snapshot(db, user_id, scene_id)
        path = await _public_path(db, user_id, scene.media_path) if scene and scene.status == "ready" else None
        return EpisodeOriginalAsset("image", path) if path else None
    if outfit_id := result.get("outfit_id"):
        outfit = await ready_outfit_snapshot(db, user_id, outfit_id)
        path = await _public_path(db, user_id, outfit.fullbody_url) if outfit else None
        return EpisodeOriginalAsset("image", path) if path else None
    return None
