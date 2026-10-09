"""桌面视频备份的冻结资料、引用映射与素材校验。"""

import hashlib
import json
from pathlib import Path
from typing import Any

from components import SETTINGS
from modules.companion import DesktopVideoAsset, DesktopVideoProgress, DesktopVisualSnapshot, desktop_visual_hash
from PIL import Image

from services.domains.assets import asset_paths
from services.infrastructure.assets import normalize_asset_reference, parse_companion_asset_path


def restore_desktop_payload(table: str, payload: dict[str, Any], id_map: dict[str, dict[str, int | str]]) -> None:
    if table == "desktop_video_sets":
        snapshot = DesktopVisualSnapshot.model_validate_json(payload["context_json"])
        avatar_id = (
            id_map.get("avatar_assets", {}).get(str(snapshot.identity.avatar_id))
            if payload.get("avatar_id") is not None
            else 0
        )
        scene_id = (
            id_map.get("companion_scenes", {}).get(str(snapshot.scene_id)) if payload.get("scene_id") is not None else 0
        )
        outfit_id = (
            id_map.get("companion_outfits", {}).get(str(snapshot.outfit_id))
            if payload.get("outfit_id") is not None
            else None
        )
        if avatar_id is None or scene_id is None or (payload.get("outfit_id") is not None and outfit_id is None):
            raise ValueError("桌面生活的身份、穿着或场景缺少映射，请一并恢复对应类别。")
        if any(
            payload.get(field) is not None and payload[field] != mapped
            for field, mapped in (("avatar_id", avatar_id), ("outfit_id", outfit_id), ("scene_id", scene_id))
        ):
            raise ValueError("桌面生活组合与冻结资料的引用不一致。")
        snapshot = snapshot.model_copy(
            update={
                "identity": snapshot.identity.model_copy(update={"avatar_id": int(avatar_id)}),
                "outfit_id": int(outfit_id) if outfit_id is not None else None,
                "scene_id": int(scene_id),
            },
        )
        payload["context_json"] = snapshot.model_dump_json()
        payload["context_hash"] = desktop_visual_hash(snapshot)
        if payload.get("status") == "preparing":
            payload["status"] = "failed"
    elif table == "desktop_video_actions":
        if payload.get("set_id") is None or payload.get("kind") not in {"loop", "once"}:
            raise ValueError("桌面生活动作的组合或播放方式无效。")
        if type(payload.get("duration_seconds")) is not int or not 1 <= payload["duration_seconds"] <= 15:
            raise ValueError("桌面生活动作时长无效。")
        for field in ("enabled", "preset"):
            if type(payload.get(field)) is not bool:
                raise ValueError("桌面生活动作开关无效。")
        for field in ("version", "attempt"):
            if type(payload.get(field)) is not int or payload[field] < 0:
                raise ValueError("桌面生活素材版本无效。")
        for field in ("use_when_json", "avoid_when_json"):
            conditions = json.loads(payload[field])
            if (
                not isinstance(conditions, list)
                or len(conditions) > 8
                or any(not isinstance(item, str) or not item.strip() or len(item) > 120 for item in conditions)
            ):
                raise ValueError("桌面生活动作使用条件无效。")
        if payload.get("status") not in {"queued", "processing", "ready", "failed", "review_pending"}:
            raise ValueError("桌面生活动作状态无效。")
        if payload.get("accepted_asset_json"):
            payload["accepted_asset_json"] = DesktopVideoAsset.model_validate_json(
                payload["accepted_asset_json"],
            ).model_dump_json()
        progress = (
            DesktopVideoProgress.model_validate_json(payload["generation_state_json"])
            if payload.get("generation_state_json")
            else None
        )
        if progress:
            progress.image_chain_json = progress.video_chain_json = None
            progress.provider_task_id = progress.download_url = None
            progress.submission_unknown = False
            payload["generation_state_json"] = progress.model_dump_json()
        if payload["status"] == "ready" and not payload.get("accepted_asset_json"):
            raise ValueError("已就绪桌面生活动作缺少已采纳素材。")
        if payload["status"] == "review_pending" and (progress is None or progress.candidate is None):
            raise ValueError("待复核桌面生活动作缺少候选素材。")
        if payload["status"] in {"queued", "processing"}:
            payload["status"] = "failed"
            payload["stage"] = "failed"
            payload["error"] = "恢复的桌面制作需要手动处理；已采纳素材仍可使用"
    elif table == "desktop_video_states":
        for field in ("pinned", "autonomous_enabled"):
            if type(payload.get(field)) is not bool:
                raise ValueError("桌面生活偏好无效。")
        for field in ("set_epoch", "version"):
            if type(payload.get(field)) is not int or payload[field] < 0:
                raise ValueError("桌面生活状态版本无效。")
            payload[field] += 1
        payload["preparation_error"] = None
        payload["selected_play_id"] = None


def validate_desktop_owned_paths(payload: dict[str, Any], user_id: int) -> None:
    references: list[str] = []
    if payload.get("context_json"):
        snapshot = DesktopVisualSnapshot.model_validate_json(payload["context_json"])
        references.extend((snapshot.identity_path, snapshot.outfit_path))
    for field in ("accepted_asset_json", "generation_state_json"):
        if payload.get(field):
            value = json.loads(payload[field])
            references.extend(asset_paths(value, user_id))
            if field == "accepted_asset_json":
                asset = DesktopVideoAsset.model_validate(value)
                references.extend((asset.video_path, asset.poster_path))
            elif value.get("candidate"):
                asset = DesktopVideoAsset.model_validate(value["candidate"])
                references.extend((asset.video_path, asset.poster_path))
            if field == "generation_state_json":
                references.extend(path for path in (value.get("pose_path"), value.get("source_path")) if path)
    for reference in references:
        parsed = parse_companion_asset_path(normalize_asset_reference(reference))
        if parsed is None or parsed[0] != user_id:
            raise ValueError("桌面生活素材不属于恢复账户。")


def validate_desktop_files(
    rows: dict[str, list[dict[str, Any]]],
    extract_root: Path,
    source_user_id: int,
    target_user_id: int,
) -> None:
    source_root = (extract_root / "files").resolve()
    target_root = Path(SETTINGS.data_dir).resolve()

    def effective_file(reference: str) -> Path:
        path = normalize_asset_reference(reference)
        parsed = parse_companion_asset_path(path)
        if parsed is None or parsed[0] != source_user_id:
            raise ValueError("桌面生活素材不属于备份账户。")
        source = source_root / path
        if not source.resolve().is_relative_to(source_root):
            raise ValueError("桌面生活素材路径越界。")
        if "/" not in parsed[1]:
            target = target_root / "companion-assets" / str(target_user_id) / parsed[1]
            if target.is_file() and target.resolve().is_relative_to(target_root):
                return target
        return source

    for row in rows.get("desktop_video_sets", []):
        snapshot = DesktopVisualSnapshot.model_validate_json(row["context_json"])
        if row.get("context_hash") != desktop_visual_hash(snapshot):
            raise ValueError("桌面生活组合内容版本不匹配。")
        for reference, expected_hash in (
            (snapshot.identity_path, snapshot.identity_hash),
            (snapshot.outfit_path, snapshot.outfit_hash),
        ):
            file = effective_file(reference)
            try:
                with file.open("rb") as input_file:
                    actual_hash = hashlib.file_digest(input_file, "sha256").hexdigest()
                with Image.open(file) as image:
                    image.load()
            except OSError as exc:
                raise ValueError("桌面生活冻结参考缺失或无法读取。") from exc
            if actual_hash != expected_hash:
                raise ValueError("桌面生活冻结参考与文件内容不匹配。")
    for row in rows.get("desktop_video_actions", []):
        assets = []
        if row.get("accepted_asset_json"):
            assets.append(DesktopVideoAsset.model_validate_json(row["accepted_asset_json"]))
        if row.get("generation_state_json"):
            progress = DesktopVideoProgress.model_validate_json(row["generation_state_json"])
            if row.get("status") == "review_pending" and progress.candidate:
                assets.append(progress.candidate)
        for asset in assets:
            video, poster = effective_file(asset.video_path), effective_file(asset.poster_path)
            if not video.is_file() or video.suffix.lower() != ".mp4" or not poster.is_file():
                raise ValueError("桌面生活视频或封面缺失。")
            try:
                with Image.open(poster) as image:
                    image.load()
                    if image.size != (asset.width, asset.height):
                        raise ValueError("桌面生活封面与视频交付尺寸不一致。")
            except OSError as exc:
                raise ValueError("桌面生活封面无法读取。") from exc
