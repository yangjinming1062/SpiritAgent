"""影像备份的冻结资料、引用映射与素材校验。"""

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

from features.media.chain import MediaChainState
from features.media.character_images import ImageChainState

from .schemas import (
    VISUAL_MAX_EDGE,
    ComboSnapshot,
    PresentedVisual,
    VisualAsset,
    parse_combo_snapshot,
    parse_job_progress,
)

type IdMap = dict[str, dict[str, int | str]]


def _mapped(id_map: IdMap, table: str, value: int | None) -> int | None:
    if value is None:
        return None
    mapped = id_map.get(table, {}).get(str(value))
    return int(mapped) if isinstance(mapped, int) else None


def _remap_snapshot(snapshot: ComboSnapshot, id_map: IdMap) -> ComboSnapshot:
    """快照素材 ID 映射不到置 None；冻结路径、哈希与描述继续承担制作依据。"""

    avatar_id = _mapped(id_map, "avatar_assets", snapshot.identity.avatar_id)
    outfit_id = _mapped(id_map, "companion_outfits", snapshot.outfit.material_id)
    scene_id = _mapped(id_map, "companion_scenes", snapshot.scene.material_id)
    action_id = _mapped(id_map, "action_definitions", snapshot.action.action_id)
    return snapshot.model_copy(
        update={
            "identity": snapshot.identity.model_copy(update={"avatar_id": avatar_id or 0}),
            "outfit": snapshot.outfit.model_copy(update={"material_id": outfit_id}),
            "scene": snapshot.scene.model_copy(update={"material_id": scene_id}),
            "action": snapshot.action.model_copy(update={"action_id": action_id}),
        },
    )


def _sanitize_progress(value: str | None) -> str | None:
    """供应商与队列句柄不能跨恢复生效；提交中的结果按未知占用保留。"""

    progress = parse_job_progress(value)
    if progress is None:
        return None
    video_unknown = False
    if progress.video_chain_json:
        chain = MediaChainState.model_validate_json(progress.video_chain_json)
        video_unknown = chain.stop_reason == "result_unknown" or (
            chain.phase == "submitting" and not chain.queue_request_id
        )
        progress.video_chain_json = None
        progress.video_queue = None
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
    return progress.model_dump_json()


def restore_visual_payload(table: str, payload: dict[str, Any], id_map: IdMap, *, target_user_id: int) -> None:
    if table == "visual_items":
        for field in ("width", "height"):
            if type(payload.get(field)) is not int or not 0 < payload[field] <= VISUAL_MAX_EDGE:
                raise ValueError("影像尺寸无效。")
        if payload.get("duration_ms") is not None and (
            type(payload["duration_ms"]) is not int or payload["duration_ms"] <= 0
        ):
            raise ValueError("影像时长无效。")
        if payload.get("media_kind") not in {"image", "video"}:
            raise ValueError("影像媒体类型无效。")
        if type(payload["content_hash"]) is not str or len(payload["content_hash"]) != 64:
            raise ValueError("影像内容哈希无效。")
        if payload["media_kind"] == "image" and (payload.get("poster_path") or payload.get("duration_ms")):
            raise ValueError("图片影像不能包含封面或时长。")
        snapshot = _remap_snapshot(parse_combo_snapshot(payload["snapshot_json"]), id_map)
        payload["snapshot_json"] = snapshot.model_dump_json()
        payload["outfit_id"] = snapshot.outfit.material_id
        payload["scene_id"] = snapshot.scene.material_id
        payload["action_id"] = snapshot.action.action_id
        # 输入图链接是同表前向引用，先置空，整表写入后按原 ID 回填。
        payload["input_image_id"] = None
    elif table == "visual_jobs":
        if payload.get("purpose") not in {"create", "regenerate"}:
            raise ValueError("制作工作项类型无效。")
        if payload.get("target_kind") not in {"image", "video"}:
            raise ValueError("制作工作项目标类型无效。")
        if payload["purpose"] == "regenerate" and payload.get("target_visual_id") is None:
            raise ValueError("重做工作项必须保留目标影像。")
        if payload.get("state") not in {"queued", "running", "paused", "succeeded", "failed", "cancelled", "unknown"}:
            raise ValueError("制作工作项状态无效。")
        snapshot = _remap_snapshot(parse_combo_snapshot(payload["snapshot_json"]), id_map)
        payload["snapshot_json"] = snapshot.model_dump_json()
        if payload.get("candidates_json"):
            candidates = json.loads(payload["candidates_json"])
            if not isinstance(candidates, list):
                raise ValueError("制作候选记录无效。")
            payload["candidates_json"] = json.dumps(
                [VisualAsset.model_validate(entry).model_dump(mode="json") for entry in candidates],
                ensure_ascii=False,
            )
        payload["progress_json"] = _sanitize_progress(payload.get("progress_json"))
        if payload["state"] in {"queued", "running"}:
            progress = parse_job_progress(payload["progress_json"])
            if progress is not None:
                progress.paused_by_user = True
                progress.failure_reason = "result_unknown" if progress.submission_unknown else "temporary"
                payload["progress_json"] = progress.model_dump_json()
            payload["state"] = "paused"
            payload["stage"] = "paused"
            payload["error"] = "恢复的制作需要手动处理；已有成品仍可使用"
    elif table == "visual_presentations":
        for field in ("current_snapshot_json", "loop_snapshot_json"):
            if not payload.get(field):
                continue
            frozen = PresentedVisual.model_validate_json(payload[field])
            snapshot = _remap_snapshot(parse_combo_snapshot(frozen.snapshot_json), id_map)
            visual_id = _mapped(id_map, "visual_items", frozen.id)
            if visual_id is None:
                raise ValueError("当前呈现的影像无法映射到恢复账户。")
            frozen = frozen.model_copy(
                update={
                    "id": visual_id,
                    "user_id": target_user_id,
                    "play_id": None,
                    "snapshot_json": snapshot.model_dump_json(),
                    "outfit_id": snapshot.outfit.material_id,
                    "scene_id": snapshot.scene.material_id,
                    "action_id": snapshot.action.action_id,
                    "input_image_id": _mapped(id_map, "visual_items", frozen.input_image_id),
                },
            )
            payload[field] = frozen.model_dump_json()
        for field in ("pinned", "autonomous_enabled"):
            if type(payload.get(field)) is not bool:
                raise ValueError("影像呈现偏好无效。")
        for field in ("presentation_epoch", "state_revision"):
            if type(payload.get(field)) is not int or payload[field] < 0:
                raise ValueError("影像呈现版本无效。")
            payload[field] += 1
        # 恢复后的待播指令跨账户与代次失效，只保留呈现指针。
        payload["pending_command_json"] = None


def validate_visual_owned_paths(payload: dict[str, Any], user_id: int) -> None:
    references: list[str] = []
    for field in (
        "media_path",
        "poster_path",
        "input_image_path",
        "snapshot_json",
        "progress_json",
        "candidates_json",
        "current_snapshot_json",
        "loop_snapshot_json",
    ):
        if payload.get(field):
            references.extend(asset_paths(payload[field], None))
    for reference in references:
        parsed = parse_companion_asset_path(normalize_asset_reference(reference))
        if parsed is None or parsed[0] != user_id:
            raise ValueError("影像素材不属于恢复账户。")


def validate_visual_files(
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
            raise ValueError("影像素材不属于备份账户。")
        source = source_root / path
        if not source.resolve().is_relative_to(source_root):
            raise ValueError("影像素材路径越界。")
        if "/" not in parsed[1]:
            target = target_root / "companion-assets" / str(target_user_id) / parsed[1]
            if target.is_file() and target.resolve().is_relative_to(target_root):
                return target
        return source

    def validate_image(reference: str | None) -> None:
        if not reference:
            return
        try:
            with Image.open(effective_file(reference)) as image:
                image.load()
                if image.width < 1 or image.height < 1:
                    raise ValueError("影像图片画幅无效。")
        except OSError as exc:
            raise ValueError("影像图片缺失或无法读取。") from exc

    def validate_video(reference: str | None) -> tuple[int, int, int] | None:
        if not reference:
            return None
        try:
            source = effective_file(reference)
            probe = probe_video(source)
            with Image.open(BytesIO(extract_desktop_first_frame(source))) as frame:
                return frame.width, frame.height, round(probe.duration_seconds * 1000)
        except (OSError, VideoProcessError) as exc:
            raise ValueError("影像视频缺失或无法读取。") from exc

    for row in rows.get("visual_items", []):
        snapshot = parse_combo_snapshot(row["snapshot_json"])
        for reference, expected_hash in (
            (snapshot.identity_path, snapshot.identity_hash),
            (snapshot.portrait_path, snapshot.portrait_hash),
            (snapshot.outfit.path, snapshot.outfit.content_hash),
            (snapshot.scene.path, snapshot.scene.content_hash),
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
                raise ValueError("影像冻结参考缺失或无法读取。") from exc
            if actual_hash != expected_hash:
                raise ValueError("影像冻结参考与文件内容不匹配。")
        media = effective_file(row["media_path"])
        try:
            with media.open("rb") as handle:
                actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as exc:
            raise ValueError("影像成品缺失或无法读取。") from exc
        if actual_hash != row["content_hash"]:
            raise ValueError("影像成品与内容哈希不匹配。")
        if row["media_kind"] == "video":
            validate_image(row.get("input_image_path"))
            validate_image(row.get("poster_path"))
            metadata = validate_video(row["media_path"])
            if (
                metadata is not None
                and row.get("duration_ms") is not None
                and abs(metadata[2] - row["duration_ms"]) > 1000
            ):
                raise ValueError("影像视频时长与记录不一致。")
        else:
            validate_image(row["media_path"])
    for row in rows.get("visual_jobs", []):
        for field in ("progress_json", "candidates_json"):
            if not row.get(field):
                continue
            value = json.loads(row[field])
            for reference in asset_paths(value, source_user_id):
                validate_image(reference) if _looks_like_image(reference) else validate_video(reference)


def _looks_like_image(reference: str) -> bool:
    return Path(normalize_asset_reference(reference)).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
