"""动作资产恢复：身份映射、冻结任务与可播目录重建。"""

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from components import SETTINGS
from modules.companion import CharacterCardSnapshot, CompanionAction, CompanionActionPack, make_action_reference_hash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import build_catalog_manifest
from services.infrastructure.assets import build_data_uri, compute_file_sha256, image_mime_for_extension

from .file_packing import UrlRewriter


def restore_action_payload(
    table: str,
    payload: dict[str, Any],
    id_map: dict[str, dict[str, int | str]],
    user_id: int,
) -> None:
    asset_paths: dict[str, str] = {}
    for path in _asset_references(payload):
        if not isinstance(path, str):
            raise ValueError("Invalid action asset path")
        parts = Path(path).parts
        if len(parts) == 3 and parts[0] == "companion-assets":
            asset_paths[path] = f"companion-assets/{user_id}/{parts[-1]}"
    # 已清理的中间文件不在复制清单中，仍须迁移引用，避免后续清理触及源用户目录。
    payload.update(UrlRewriter(asset_paths).rewrite(payload))
    if table == "companion_actions":
        if payload.get("pack_id") is None:
            raise ValueError("Action pack is missing from backup")
        if payload.get("status") in {"queued", "processing", "result_unknown", "review"}:
            payload["status"] = "failed"
            payload["error"] = "恢复的动作任务需要手动处理；请先核对已有生成结果"
        # 保留句柄、提交未知标记与已付费产物，手动重试仍遵循原任务的幂等守卫。
        return
    context = _json_object(payload, "context_json")
    _json_object(payload, "canvas_spec")
    if context:
        identity = CharacterCardSnapshot.model_validate(context["identity"])
        avatar_id = id_map.get("avatar_assets", {}).get(str(identity.avatar_id))
        if avatar_id is None or avatar_id != payload.get("avatar_id"):
            raise ValueError("Action identity is missing from backup")
        context["identity"] = identity.model_copy(update={"avatar_id": int(avatar_id)}).model_dump()
        outfit_id = context.get("active_outfit_id")
        if outfit_id is not None:
            mapped = id_map.get("companion_outfits", {}).get(str(outfit_id))
            if mapped is None:
                raise ValueError("Action context outfit is missing from backup")
            context["active_outfit_id"] = mapped
        # 中断的参考准备由用户显式重试，不能把运行中状态带入新实例。
        if context.get("reference_alignment") == "running":
            context["reference_alignment"] = "pending"
        payload["context_json"] = json.dumps(context, ensure_ascii=False)
    if payload.get("status") == "processing":
        payload["status"] = "failed"
        payload["error"] = "恢复的视频任务需要手动处理"
    if payload.get("status") != "ready":
        payload["active"] = False
    payload["manifest_path"] = ""
    payload["catalog_version"] = 0
    payload["content_hash"] = ""


def _json_object(row: dict[str, Any], field: str) -> dict[str, Any]:
    value = json.loads(row.get(field) or "{}")
    if not isinstance(value, dict):
        raise ValueError(f"Action {field} must be an object")
    return value


def _asset_path(path: str, user_id: int, root: Path) -> Path:
    if not isinstance(path, str) or not path or "\\" in path or ".." in path:
        raise ValueError("Invalid action asset path")
    relative = Path(path)
    allowed = len(relative.parts) == 3 and relative.parts[:2] == ("companion-assets", str(user_id))
    target = root / relative
    if not allowed or target.resolve() != target:
        raise ValueError("Action asset is outside its user scope")
    return target


def _asset_file(path: str, user_id: int) -> Path:
    target = _asset_path(path, user_id, Path(SETTINGS.data_dir).resolve())
    if not target.is_file():
        raise ValueError("Restored action asset file is missing")
    return target


def _asset_references(value: Any) -> Iterator[str]:
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return
        yield from _asset_references(parsed)
    elif isinstance(value, list):
        for item in value:
            yield from _asset_references(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "path" or key.endswith("_path"):
                if item is not None and item != "":
                    yield item
            elif key == "artifacts":
                if not isinstance(item, list):
                    raise ValueError("Action artifacts must be a list")
                yield from item
            elif key in {
                "context_json",
                "reference_chain",
                "generation_state_json",
                "pose_generation_state_json",
                "result_json",
                "candidates",
                "clip",
            }:
                # 只进入资产结构；反馈、脚本和人设中的 JSON 文本不代表文件引用。
                yield from _asset_references(item)


def validate_action_files(
    rows: dict[str, list[dict[str, Any]]],
    extract_root: Path,
    source_user_id: int,
    target_user_id: int,
) -> None:
    """清理目标行前验证路径和可播素材；同名文件按实际恢复规则检查目标内容。"""
    source_root = (extract_root / "files").resolve()
    target_root = Path(SETTINGS.data_dir).resolve()

    def effective_file(path: str) -> Path:
        source = _asset_path(path, source_user_id, source_root)
        relative = source.relative_to(source_root)
        if relative.parts[0] == "companion-assets":
            relative = Path("companion-assets", str(target_user_id), relative.name)
        target = _asset_path(relative.as_posix(), target_user_id, target_root)
        return target if target.exists() else source

    outfits = {str(row["id"]): row for row in rows.get("companion_outfits", [])}
    for pack in rows.get("companion_action_packs", []):
        outfit = outfits.get(str(pack.get("outfit_id")))
        if outfit is not None and not effective_file(outfit["fullbody_url"]).is_file():
            raise ValueError("Action outfit reference is missing from backup and destination")
    for table in ("companion_action_packs", "companion_actions"):
        for row in rows.get(table, []):
            # 中间候选可能已清理；仍须限制恢复后重试、删除等操作的路径归属。
            for path in _asset_references(row):
                effective_file(path)
            if table == "companion_actions" and row.get("status") == "succeeded":
                video = effective_file(row.get("video_path"))
                if not video.is_file():
                    raise ValueError("Action video is missing from backup and destination")
                if row.get("video_hash") and compute_file_sha256(video) != row["video_hash"]:
                    raise ValueError("Action video does not match its content hash")
                if row.get("hitmask_path") and not effective_file(row["hitmask_path"]).is_file():
                    raise ValueError("Action hitmask is missing from backup and destination")
            if (
                table == "companion_action_packs"
                and row.get("status") == "ready"
                and row.get("cover_path")
                and not effective_file(row["cover_path"]).is_file()
            ):
                raise ValueError("Action cover is missing from backup and destination")


async def restore_action_catalogs(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    id_map: dict[str, dict[str, int | str]],
    user_id: int,
    rewriter: UrlRewriter,
    *,
    write_files: bool,
) -> None:
    outfits = {str(row["id"]): row for row in rows.get("companion_outfits", [])}
    for raw in rows.get("companion_action_packs", []):
        pack = await db.get(CompanionActionPack, id_map["companion_action_packs"][str(raw["id"])])
        jobs = list(await db.scalars(select(CompanionAction).where(CompanionAction.pack_id == pack.id)))
        if any(job.user_id != user_id or job.outfit_id is not None and job.outfit_id != pack.outfit_id for job in jobs):
            raise ValueError("Action belongs to a different outfit or user")
        if write_files:
            outfit = outfits.get(str(raw.get("outfit_id")))
            if outfit is not None:
                original_path = outfit["fullbody_url"]
                restored_path = rewriter(original_path)
                path = _asset_file(restored_path, user_id)
                mime = image_mime_for_extension(path.suffix)
                if mime is None:
                    raise ValueError("外观参考图格式无效")
                reference = await asyncio.to_thread(lambda: build_data_uri(path.read_bytes(), mime))
                original_hash = make_action_reference_hash(outfit["id"], original_path, raw.get("avatar_id"), reference)
                # 只迁移仍对应原外观的指纹；历史失效包不能在导入后意外变成当前身份。
                if pack.reference_hash == original_hash:
                    pack.reference_hash = make_action_reference_hash(
                        pack.outfit_id,
                        restored_path,
                        pack.avatar_id,
                        reference,
                    )
                    for job in jobs:
                        if job.reference_hash == original_hash:
                            job.reference_hash = pack.reference_hash
            for job in jobs:
                if job.status == "succeeded":
                    video = _asset_file(job.video_path, user_id)
                    if job.video_hash and await asyncio.to_thread(compute_file_sha256, video) != job.video_hash:
                        raise ValueError("Restored action video does not match its content hash")
                    if job.hitmask_path:
                        _asset_file(job.hitmask_path, user_id)
        if pack.status != "ready":
            continue
        manifest = await build_catalog_manifest(db, pack)
        if manifest is None:
            pack.status = "failed"
            pack.active = False
            pack.error = "恢复的动作尚未齐备，请手动处理未完成的动作"
            continue
        if not write_files:
            continue
        if manifest.cover_path:
            _asset_file(manifest.cover_path, user_id)
        payload = manifest.model_dump_json()
        filename = f"action-catalog-{pack.id}-restored-{uuid4().hex}.json"
        path = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            rewriter.created.append(path)
            output.write(payload)
        pack.manifest_path = f"companion-assets/{user_id}/{filename}"
        pack.catalog_version = manifest.catalog_version
        pack.content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    await db.flush()
