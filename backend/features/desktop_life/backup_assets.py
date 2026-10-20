"""桌面视频备份的冻结资料、引用映射与素材校验。"""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from infrastructure.assets.asset_store import normalize_asset_reference
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.assets.references import asset_paths
from infrastructure.config import SETTINGS
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video
from PIL import Image

from features.action_library.identity import source_visual_key
from features.desktop_life.schemas import (
    DESKTOP_MAX_EDGE,
    DesktopUploadSnapshot,
    DesktopVideoAsset,
    DesktopVideoProgress,
    DesktopVisualSnapshot,
    desktop_visual_hash,
    parse_desktop_context,
)
from features.media.chain import MediaChainState
from features.media.character_images import ImageChainState


def restore_desktop_payload(table: str, payload: dict[str, Any], id_map: dict[str, dict[str, int | str]]) -> None:
    if table == "desktop_video_sets":
        for field in ("target_width", "target_height", "resolution_revision"):
            if (
                type(payload.get(field)) is not int
                or payload[field] <= 0
                or field != "resolution_revision"
                and payload[field] > DESKTOP_MAX_EDGE
            ):
                raise ValueError("桌景目标分辨率或版本无效。")
        snapshot = parse_desktop_context(payload["source"], payload["context_json"])
        if isinstance(snapshot, DesktopVisualSnapshot):
            avatar_id = id_map.get("avatar_assets", {}).get(str(snapshot.identity.avatar_id))
            scene_id = id_map.get("companion_scenes", {}).get(str(snapshot.scene_id))
            outfit_id = id_map.get("companion_outfits", {}).get(str(snapshot.outfit_id))
            # 来源库可独立删除；套装冻结图片与文本承担制作依据，外键只记录可用的追溯关系。
            payload.update(avatar_id=avatar_id, scene_id=scene_id, outfit_id=outfit_id)
            snapshot = snapshot.model_copy(
                update={
                    "identity": snapshot.identity.model_copy(update={"avatar_id": int(avatar_id or 0)}),
                    "outfit_id": int(outfit_id) if outfit_id is not None else None,
                    "scene_id": int(scene_id or 0),
                },
            )
            payload["source_visual_key"] = source_visual_key(
                snapshot.identity_hash,
                snapshot.outfit_hash,
                portrait_hash=snapshot.portrait_hash,
                identity_reference_role=snapshot.identity_reference_role,
            )
        else:
            if payload["source_visual_key"] != snapshot.source_key or any(
                payload.get(field) is not None for field in ("avatar_id", "outfit_id", "scene_id")
            ):
                raise ValueError("上传桌景不能关联素材库来源。")
            if not snapshot.locked and (
                snapshot.original_image_path or snapshot.original_video_path or snapshot.reference_image_path
            ):
                raise ValueError("已保存上传素材的桌景必须锁定来源。")
            payload.update(avatar_id=None, outfit_id=None, scene_id=None)
        payload["context_json"] = snapshot.model_dump_json()
        payload["context_hash"] = desktop_visual_hash(snapshot)
        base_version = payload["base_version"]
        base_path = payload["base_image_path"]
        if (
            type(base_version) is not int
            or base_version < 0
            or (base_path is not None and (not isinstance(base_path, str) or not base_path.strip()))
            or bool(base_path) != (base_version > 0)
        ):
            raise ValueError("桌面生活基础画面版本或引用无效。")
        if type(payload.get("is_default")) is not bool or payload.get("source") not in {
            "ai",
            "image_upload",
            "video_upload",
        }:
            raise ValueError("桌景套装来源无效。")
    elif table == "desktop_video_actions":
        if payload.get("set_id") is None or payload.get("kind") not in {"loop", "once"}:
            raise ValueError("桌面生活动作的组合或播放方式无效。")
        if type(payload.get("duration_seconds")) is not int or not 1 <= payload["duration_seconds"] <= 15:
            raise ValueError("桌面生活动作时长无效。")
        if type(payload["target_duration_seconds"]) is not int or not 1 <= payload["target_duration_seconds"] <= 15:
            raise ValueError("桌面生活动作目标时长无效。")
        if not isinstance(payload["continuity"], str) or payload["continuity"] not in {"base", "independent"}:
            raise ValueError("桌面生活动作衔接方式无效。")
        triggers = json.loads(payload["allowed_triggers_json"])
        if (
            not isinstance(triggers, list)
            or not triggers
            or any(
                not isinstance(item, str) or item not in {"periodic", "user_return", "chat", "manual"}
                for item in triggers
            )
            or len(set(triggers)) != len(triggers)
        ):
            raise ValueError("桌面生活动作触发条件无效。")
        for field in ("enabled", "preset", "needs_update", "update_pending"):
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
        if payload.get("status") not in {
            "not_generated",
            "queued",
            "processing",
            "ready",
            "failed",
            "review_pending",
            "blocked",
        }:
            raise ValueError("桌面生活动作状态无效。")
        if payload["status"] == "not_generated" and (
            payload.get("generation_state_json") or payload.get("accepted_asset_json")
        ):
            raise ValueError("尚未制作的桌景动作不能包含制作进度或已采纳素材。")
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
            video_chain = (
                MediaChainState.model_validate_json(progress.video_chain_json) if progress.video_chain_json else None
            )
            video_unknown = video_chain is not None and (
                video_chain.stop_reason == "result_unknown"
                or video_chain.phase == "submitting"
                and not video_chain.queue_request_id
            )
            progress.video_chain_json = None
            progress.provider_task_id = progress.download_url = None
            progress.submission_unknown = (
                progress.submission_unknown
                or video_unknown
                or any(
                    (chain := ImageChainState.model_validate_json(chain_json)).phase == "submitting"
                    or chain.stop_reason == "result_unknown"
                    for chain_json in (progress.reference_alignment_chain_json, progress.image_chain_json)
                    if chain_json
                )
            )
            payload["generation_state_json"] = progress.model_dump_json()
        payload["update_pending"] = False
        if payload["status"] == "ready" and not payload.get("accepted_asset_json"):
            raise ValueError("已就绪桌面生活动作缺少已采纳素材。")
        if payload["status"] == "review_pending" and (
            progress is None or (progress.candidate is None and not progress.pose_path)
        ):
            raise ValueError("待确认桌面生活动作缺少候选图片或视频。")
        if payload["status"] in {"queued", "processing"}:
            payload["status"] = "failed"
            payload["stage"] = "paused"
            payload["error"] = "恢复的桌面制作需要手动处理；已采纳素材仍可使用"
    elif table == "desktop_video_states":
        width, height = payload.get("target_width"), payload.get("target_height")
        if (
            (width is None) != (height is None)
            or width is not None
            and (
                type(width) is not int
                or not 0 < width <= DESKTOP_MAX_EDGE
                or type(height) is not int
                or not 0 < height <= DESKTOP_MAX_EDGE
            )
        ):
            raise ValueError("默认桌景待创建分辨率无效。")
        for field in ("pinned", "autonomous_enabled", "default_created"):
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
    if payload.get("base_image_path"):
        references.append(payload["base_image_path"])
    if payload.get("context_json"):
        snapshot = parse_desktop_context(payload["source"], payload["context_json"])
        if isinstance(snapshot, DesktopUploadSnapshot):
            references.extend(
                path
                for path in (
                    snapshot.original_image_path,
                    snapshot.original_video_path,
                    snapshot.reference_image_path,
                )
                if path
            )
        else:
            references.extend(
                path
                for path in (
                    snapshot.identity_path,
                    snapshot.outfit_path,
                    snapshot.portrait_path,
                    snapshot.scene_path,
                )
                if path
            )
    for field in ("accepted_asset_json", "generation_state_json"):
        if payload.get(field):
            value = json.loads(payload[field])
            references.extend(asset_paths(value, user_id))
            if field == "accepted_asset_json":
                asset = DesktopVideoAsset.model_validate(value)
                references.extend(
                    path
                    for path in (
                        asset.video_path,
                        asset.poster_path,
                        asset.image_path,
                        asset.base_image_path,
                        asset.source_image_path,
                        asset.source_video_path,
                    )
                    if path
                )
            elif value.get("candidate"):
                asset = DesktopVideoAsset.model_validate(value["candidate"])
                references.extend(
                    path
                    for path in (
                        asset.video_path,
                        asset.poster_path,
                        asset.image_path,
                        asset.base_image_path,
                        asset.source_image_path,
                        asset.source_video_path,
                    )
                    if path
                )
            if field == "generation_state_json":
                progress = DesktopVideoProgress.model_validate(value)
                refinement = progress.video_refinement
                if refinement is not None:
                    references.extend(
                        (refinement.video_path, refinement.original_video_path, refinement.image_path),
                    )
                references.extend(
                    path
                    for path in (
                        value.get("pose_path"),
                        value.get("previous_pose_path"),
                        value.get("source_path"),
                        value.get("base_image_path"),
                        value.get("source_image_path"),
                        progress.aligned_outfit_path,
                    )
                    if path
                )
                for chain_json in (
                    progress.reference_alignment_chain_json,
                    progress.image_chain_json,
                    progress.previous_image_chain_json,
                ):
                    if chain_json:
                        references.extend(ImageChainState.model_validate_json(chain_json).stored_paths())
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

    def validate_base_image(reference: str | None) -> None:
        if not reference:
            return
        try:
            with Image.open(effective_file(reference)) as image:
                image.load()
                if image.width < 1 or image.height < 1:
                    raise ValueError("桌面生活基础画面画幅无效。")
        except OSError as exc:
            raise ValueError("桌面生活基础画面缺失或无法读取。") from exc

    def validate_source_video(reference: str | None) -> tuple[int, int, int] | None:
        if not reference:
            return None
        try:
            source = effective_file(reference)
            probe = probe_video(source)
            with Image.open(BytesIO(extract_desktop_first_frame(source))) as frame:
                return frame.width, frame.height, round(probe.duration_seconds * 1000)
        except (OSError, VideoProcessError) as exc:
            raise ValueError("桌面生活原始视频缺失或无法读取。") from exc

    for row in rows.get("desktop_video_sets", []):
        validate_base_image(row["base_image_path"])
        snapshot = parse_desktop_context(row["source"], row["context_json"])
        if row.get("context_hash") != desktop_visual_hash(snapshot):
            raise ValueError("桌面生活组合内容版本不匹配。")
        if isinstance(snapshot, DesktopUploadSnapshot):
            validate_base_image(snapshot.original_image_path)
            validate_base_image(snapshot.reference_image_path)
            validate_source_video(snapshot.original_video_path)
            continue
        for reference, expected_hash in (
            (snapshot.identity_path, snapshot.identity_hash),
            (snapshot.outfit_path, snapshot.outfit_hash),
            (snapshot.portrait_path, snapshot.portrait_hash),
            (snapshot.scene_path, snapshot.scene_hash),
        ):
            if not reference:
                continue
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
            validate_base_image(progress.base_image_path)
            validate_base_image(progress.pose_path)
            validate_base_image(progress.previous_pose_path)
            validate_base_image(progress.source_image_path)
            validate_base_image(progress.aligned_outfit_path)
            if (
                progress.aligned_outfit_path
                and hashlib.sha256(
                    effective_file(progress.aligned_outfit_path).read_bytes(),
                ).hexdigest()
                != progress.aligned_outfit_hash
            ):
                raise ValueError("桌面生活派生造型参考与冻结内容不一致。")
            for chain_json in (
                progress.reference_alignment_chain_json,
                progress.image_chain_json,
                progress.previous_image_chain_json,
            ):
                if not chain_json:
                    continue
                image_chain = ImageChainState.model_validate_json(chain_json)
                if image_chain.phase == "complete":
                    if image_chain.inputs is None:
                        raise ValueError("桌面图片制作缺少冻结输入。")
                    selected = [
                        candidate
                        for slot in range(image_chain.inputs.n)
                        if (candidate := image_chain.best(slot)) is not None
                    ]
                    image_paths = {candidate.path for candidate in selected} | {
                        path for candidate in selected for path in candidate.artifacts
                    }
                else:
                    image_paths = image_chain.stored_paths()
                for reference in image_paths:
                    validate_base_image(reference)
            if progress.video_refinement is not None:
                refinement = progress.video_refinement
                validate_base_image(refinement.image_path)
                metadata = validate_source_video(refinement.video_path)
                if metadata != (refinement.width, refinement.height, refinement.duration_ms):
                    raise ValueError("桌面生活参考视频与冻结资料不一致。")
                validate_source_video(refinement.original_video_path)
            if progress.candidate:
                assets.append(progress.candidate)
        for asset in assets:
            validate_base_image(asset.base_image_path)
            validate_base_image(asset.image_path)
            validate_base_image(asset.source_image_path)
            validate_source_video(asset.source_video_path)
            video, poster = effective_file(asset.video_path), effective_file(asset.poster_path)
            if not video.is_file() or not poster.is_file():
                raise ValueError("桌面生活视频或封面缺失。")
            try:
                with Image.open(BytesIO(extract_desktop_first_frame(video))) as frame:
                    if frame.size != (asset.width, asset.height):
                        raise ValueError("桌面生活视频尺寸与资产记录不一致。")
                with Image.open(poster) as image:
                    image.load()
            except VideoProcessError as exc:
                raise ValueError("桌面生活视频无法读取。") from exc
            except OSError as exc:
                raise ValueError("桌面生活封面无法读取。") from exc
