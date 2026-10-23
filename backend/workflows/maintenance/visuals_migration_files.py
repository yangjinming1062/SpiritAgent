"""一次性影像迁移的文件核验，预检与在线迁移共用。"""

import json
from io import BytesIO
from pathlib import Path
from typing import Any

from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import probe_video
from PIL import Image

from workflows.maintenance.visuals_migration import MigrationPlan, file_sha256, resolve_asset_path


def verify_migration_files(plan: MigrationPlan, data_dir: Path) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}

    def check(path: str | None, user_id: int, kind: str, expected_hash: str | None = None) -> dict[str, Any] | None:
        if not path:
            return None
        parsed = parse_companion_asset_path(path)
        if path not in records:
            record: dict[str, Any] = {"path": path, "kind": kind, "error": None}
            records[path] = record
            resolved = resolve_asset_path(data_dir, path)
            try:
                if parsed is None or parsed[0] != user_id or resolved is None:
                    raise ValueError("文件不属于迁移账户或路径越界")
                if not resolved.is_file():
                    raise ValueError("引用文件缺失")
                record["bytes"] = resolved.stat().st_size
                record["sha256"] = file_sha256(resolved)
                if kind == "video":
                    probe = probe_video(resolved)
                    with Image.open(BytesIO(extract_desktop_first_frame(resolved))) as image:
                        image.load()
                        record["width"], record["height"] = image.size
                    record["duration_ms"] = round(probe.duration_seconds * 1000)
                    if record["duration_ms"] <= 0:
                        raise ValueError("视频时长无效")
                else:
                    with Image.open(resolved) as image:
                        image.load()
                        record["width"], record["height"] = image.size
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
        record = records[path]
        if parsed is None or parsed[0] != user_id:
            record["error"] = "文件不属于迁移账户"
        if expected_hash and record.get("sha256") and record["sha256"] != expected_hash:
            record["error"] = "文件校验和与冻结参考不一致"
        return record

    def combo_references(snapshot_json: str, user_id: int) -> None:
        snapshot = json.loads(snapshot_json)
        for path_key, hash_key in (("identity_path", "identity_hash"), ("portrait_path", "portrait_hash")):
            check(snapshot.get(path_key), user_id, "image", snapshot.get(hash_key))
        for key in ("outfit", "scene"):
            material = snapshot[key]
            check(material["path"], user_id, "image", material["content_hash"])

    for item in plan.visual_items:
        user_id = item["user_id"]
        combo_references(item["snapshot_json"], user_id)
        media = check(item["media_path"], user_id, item["media_kind"], item.get("content_hash"))
        check(item.get("poster_path"), user_id, "image")
        check(item.get("input_image_path"), user_id, "image", item.get("input_image_hash"))
        if media and not media["error"]:
            item["width"], item["height"] = media["width"], media["height"]
            if item["media_kind"] == "video":
                item["duration_ms"] = media["duration_ms"]
    for job in plan.visual_jobs:
        user_id = job["user_id"]
        combo_references(job["snapshot_json"], user_id)
        progress = json.loads(job["progress_json"]) if job.get("progress_json") else {}
        for key in ("pose_path", "source_image_path", "previous_pose_path", "aligned_outfit_path"):
            check(progress.get(key), user_id, "image")
        check(progress.get("source_path"), user_id, "video")
        candidate = progress.get("candidate")
        if candidate:
            check(candidate.get("media_path"), user_id, candidate["media_kind"])
            check(candidate.get("poster_path"), user_id, "image")
        review = progress.get("opening_review") or {}
        if review.get("status") == "complete" and review.get("verdict") == "pass":
            check(progress.get("pose_path"), user_id, "image", review.get("image_hash"))
        refinement = progress.get("video_refinement") or {}
        check(refinement.get("video_path"), user_id, "video")
        check(refinement.get("original_video_path"), user_id, "video")
        check(refinement.get("image_path"), user_id, "image")
    return list(records.values())
