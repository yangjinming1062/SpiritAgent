"""动作资产恢复：身份映射、冻结任务与可播目录重建。"""

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from components import SETTINGS
from modules.companion import CharacterCardSnapshot, CompanionAction, CompanionActionPack, make_action_reference_hash
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import (
    AcceptedActionAsset,
    AcceptedImageActionAsset,
    AcceptedVideoActionAsset,
    accepted_action_asset,
    build_catalog_manifest,
    parse_accepted_action_asset_json,
)
from services.infrastructure.assets import (
    build_data_uri,
    compute_file_sha256,
    image_mime_for_extension,
    pack_asset_directory,
    parse_companion_asset_path,
)

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
        parts = PurePosixPath(path).parts
        if len(parts) >= 3 and parts[0] == "companion-assets":
            asset_paths[path] = PurePosixPath("companion-assets", str(user_id), *parts[2:]).as_posix()
    # 已清理的中间文件不在复制清单中，仍须迁移引用，避免后续清理触及源用户目录。
    payload.update(UrlRewriter(asset_paths).rewrite(payload))
    if table == "companion_actions":
        _validate_action_media_type(payload)
        if payload.get("accepted_asset_json"):
            payload["accepted_asset_json"] = parse_accepted_action_asset_json(
                payload["accepted_asset_json"],
            ).model_dump_json()
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
        payload["error"] = "恢复的动作任务需要手动处理"
    if payload.get("status") != "ready":
        payload["active"] = False
    payload["manifest_path"] = ""
    payload["catalog_version"] = 0
    payload["content_hash"] = ""


def _validate_action_media_type(row: dict[str, Any]) -> None:
    if row.get("media_type") not in {"image", "video"}:
        raise ValueError("Action media type is invalid")
    if row["media_type"] == "image" and any(
        row.get(field) is not None
        for field in ("kind", "target_duration_seconds", "actual_duration_ms", "frames", "loopable", "hitmask_fps")
    ):
        raise ValueError("Static action image cannot contain video parameters")


def _backup_accepted_asset(row: dict[str, Any]) -> AcceptedActionAsset | None:
    _validate_action_media_type(row)
    if row.get("accepted_asset_json"):
        return parse_accepted_action_asset_json(row["accepted_asset_json"])
    if row.get("status") != "succeeded":
        return None
    model = AcceptedImageActionAsset if row["media_type"] == "image" else AcceptedVideoActionAsset
    return model.model_validate({field: row.get(field) for field in model.model_fields})


def _validate_image_asset(path: Path, asset: AcceptedImageActionAsset) -> None:
    clip = asset.parse_result().clip
    try:
        with Image.open(path) as image:
            if image.format not in {"PNG", "WEBP"} or getattr(image, "is_animated", False):
                raise ValueError("Action image must be a static PNG or WebP")
            if image.size != (clip.width, clip.height):
                raise ValueError("Action image dimensions do not match its metadata")
            image.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Action image cannot be decoded") from exc


def _json_object(row: dict[str, Any], field: str) -> dict[str, Any]:
    value = json.loads(row.get(field) or "{}")
    if not isinstance(value, dict):
        raise ValueError(f"Action {field} must be an object")
    return value


def _asset_path(path: str, user_id: int, root: Path) -> Path:
    if not isinstance(path, str) or not path or "\\" in path or ".." in path:
        raise ValueError("Invalid action asset path")
    relative = Path(path)
    parsed = parse_companion_asset_path(path)
    allowed = parsed is not None and parsed[0] == user_id
    target = root / relative
    if not allowed or target.resolve() != target:
        raise ValueError("Action asset is outside its user scope")
    return target


def _asset_file(path: str, user_id: int) -> Path:
    target = _asset_path(path, user_id, Path(SETTINGS.data_dir).resolve())
    if not target.is_file():
        raise ValueError("Restored action asset file is missing")
    return target


def _asset_references(value: Any, depth: int = 0) -> Iterator[str]:
    if depth > 64:
        raise ValueError("Action asset metadata is nested too deeply")
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except RecursionError:
            raise ValueError("Action asset metadata is nested too deeply") from None
        except json.JSONDecodeError:
            return
        yield from _asset_references(parsed, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _asset_references(item, depth + 1)
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
                "accepted_asset_json",
                "reference_chain",
                "generation_state_json",
                "pose_generation_state_json",
                "result_json",
                "candidates",
                "clip",
            }:
                # 只进入资产结构；反馈、脚本和人设中的 JSON 文本不代表文件引用。
                yield from _asset_references(item, depth + 1)


def validate_action_files(
    rows: dict[str, list[dict[str, Any]]],
    extract_root: Path,
    source_user_id: int,
    target_user_id: int,
) -> None:
    """清理目标行前验证路径和可播素材；根目录资产在目标已有同名文件时按恢复规则检查其内容。"""
    source_root = (extract_root / "files").resolve()
    target_root = Path(SETTINGS.data_dir).resolve()

    def effective_file(path: str) -> Path:
        source = _asset_path(path, source_user_id, source_root)
        relative = source.relative_to(source_root)
        if relative.parts[0] == "companion-assets" and len(relative.parts) == 3:
            # 只有根目录资产与来源同名稳定可探测目标已有文件；分层目录按重新分配的 ID 落位，不按来源旧位置探测。
            target = _asset_path(f"companion-assets/{target_user_id}/{relative.name}", target_user_id, target_root)
            return target if target.is_file() else source
        return source

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
            accepted: AcceptedActionAsset | None = None
            if table == "companion_actions":
                accepted = _backup_accepted_asset(row)
            if accepted is not None:
                media = effective_file(accepted.media_path)
                if not media.is_file():
                    raise ValueError("Action media is missing from backup and destination")
                if accepted.media_hash and compute_file_sha256(media) != accepted.media_hash:
                    raise ValueError("Action media does not match its content hash")
                if accepted.media_type == "image":
                    _validate_image_asset(media, accepted)
                if accepted.hitmask_path and not effective_file(accepted.hitmask_path).is_file():
                    raise ValueError("Action hitmask is missing from backup and destination")
                if accepted.cover_path and not effective_file(accepted.cover_path).is_file():
                    raise ValueError("Action cover is missing from backup and destination")
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
                if (accepted := accepted_action_asset(job)) is not None:
                    media = _asset_file(accepted.media_path, user_id)
                    if (
                        accepted.media_hash
                        and await asyncio.to_thread(compute_file_sha256, media) != accepted.media_hash
                    ):
                        raise ValueError("Restored action media does not match its content hash")
                    if accepted.media_type == "image":
                        await asyncio.to_thread(_validate_image_asset, media, accepted)
                    if accepted.hitmask_path:
                        _asset_file(accepted.hitmask_path, user_id)
                    if accepted.cover_path:
                        _asset_file(accepted.cover_path, user_id)
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
        directory = pack_asset_directory(pack.outfit_id, pack.id)
        path = Path(SETTINGS.data_dir) / "companion-assets" / str(user_id) / directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            rewriter.created.append(path)
            output.write(payload)
        pack.manifest_path = f"companion-assets/{user_id}/{directory}/{filename}"
        pack.catalog_version = manifest.catalog_version
        pack.content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    await db.flush()
