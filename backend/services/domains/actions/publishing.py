"""动作目录发布：manifest 构建、校验与 CAS 版本推进。

位于 domains 层供 generation 收尾与 application/actions 共用；只依赖任务行与 pack 字段。
"""

import hashlib
import json
import re
from pathlib import Path

from modules.companion import REQUIRED_SYSTEM_SLOTS, CompanionActionPack
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .repository import list_pack_actions, publish_catalog

MANIFEST_SCHEMA = "spiritagent.action.pack"

_STORAGE_PATH_RE = re.compile(r"^companion-assets/\d+/[A-Za-z0-9._-]+$")


class ActionClipSpec(BaseModel):
    """目录 clip：可验证素材与播放技术参数；使用场景元数据走 catalog API。"""

    model_config = ConfigDict(extra="forbid")

    action_id: int
    asset_revision: int = 1
    system_slot: str = ""
    video_ref: str
    duration_ms: int = Field(gt=0)
    frames: int = Field(gt=0)
    loopable: bool = False
    enter_pose: str | None = None
    exit_pose: str | None = None
    hitmask_ref: str | None = None
    hitmask_grid: tuple[int, int] | None = None
    hitmask_fps: int = Field(gt=0, le=60)


class ActionPackCanvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0, le=60)


class ActionCatalogManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = MANIFEST_SCHEMA
    pack_id: int
    character_id: int | None = None
    outfit_id: int | None = None
    catalog_version: int = Field(gt=0)
    canvas: ActionPackCanvas
    clips: list[ActionClipSpec] = Field(min_length=1)
    cover_path: str | None = None
    default_action: str = "idle"


class CatalogValidationError(ValueError):
    """manifest 未通过发布校验；str 为公开文案。"""


def validate_catalog_manifest(manifest: ActionCatalogManifest) -> None:
    if manifest.schema_version != MANIFEST_SCHEMA:
        raise CatalogValidationError("manifest schema 版本不受支持")

    seen_slots: set[str] = set()
    for clip in manifest.clips:
        if not _STORAGE_PATH_RE.match(clip.video_ref):
            raise CatalogValidationError(f"资源路径不合法：{clip.video_ref}")
        if clip.system_slot:
            if clip.system_slot in seen_slots:
                raise CatalogValidationError(f"系统槽位重复：{clip.system_slot}")
            seen_slots.add(clip.system_slot)

    missing = [slot for slot in REQUIRED_SYSTEM_SLOTS if slot not in seen_slots]
    if missing:
        raise CatalogValidationError("缺少必需系统动作：" + "、".join(missing))


async def build_catalog_manifest(
    db: AsyncSession,
    pack: CompanionActionPack,
) -> ActionCatalogManifest | None:
    """合并当前成功动作构建新 manifest；必需槽位不齐返回 None（不发布，不破坏现有目录）。"""
    canvas_data = json.loads(pack.canvas_spec or "{}")
    canvas = ActionPackCanvas(
        width=canvas_data.get("width", 512),
        height=canvas_data.get("height", 512),
        fps=canvas_data.get("fps", 24),
    )

    actions = await list_pack_actions(db, pack.id, enabled_only=True)
    clips: list[ActionClipSpec] = []
    slots_present: set[str] = set()

    for action in actions:
        if action.status != "succeeded" or not action.video_path:
            continue

        duration_ms = action.actual_duration_ms or int(action.target_duration_seconds * 1000) or 2000
        frames = action.frames or int(action.target_duration_seconds * 24) or 48

        clips.append(
            ActionClipSpec(
                action_id=action.id,
                asset_revision=action.metadata_revision,
                system_slot=action.system_slot or "",
                video_ref=action.video_path,
                duration_ms=max(duration_ms, 1),
                frames=max(frames, 1),
                loopable=action.loopable,
                enter_pose=action.enter_pose,
                exit_pose=action.exit_pose,
                hitmask_ref=action.hitmask_path,
                hitmask_grid=(
                    (action.hitmask_grid_w, action.hitmask_grid_h)
                    if action.hitmask_grid_w and action.hitmask_grid_h
                    else None
                ),
                hitmask_fps=action.hitmask_fps or 24,
            ),
        )
        if action.system_slot:
            slots_present.add(action.system_slot)

    if not clips:
        return None

    missing = [slot for slot in REQUIRED_SYSTEM_SLOTS if slot not in slots_present]
    if missing:
        return None

    cover_path: str | None = None
    raw_cover = (pack.manifest_json or "").strip()
    if raw_cover and raw_cover != "{}":
        try:
            cover_path = str(json.loads(raw_cover).get("cover_path") or "") or None
        except json.JSONDecodeError:
            cover_path = None

    manifest = ActionCatalogManifest(
        pack_id=pack.id,
        character_id=pack.character_id,
        outfit_id=pack.outfit_id,
        catalog_version=pack.catalog_version + 1,
        canvas=canvas,
        clips=clips,
        cover_path=cover_path,
    )
    validate_catalog_manifest(manifest)
    return manifest


async def publish_action_catalog(
    db: AsyncSession,
    pack: CompanionActionPack,
    *,
    assets_dir: Path,
) -> int:
    """发布新目录快照：构建 manifest → 写文件 → CAS 推进版本指针。

    数据库发布失败只重试发布，不重新付费生成。
    """
    manifest = await build_catalog_manifest(db, pack)
    if manifest is None:
        raise CatalogValidationError("当前成功动作不足以发布目录")

    payload = manifest.model_dump_json()
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    filename = f"action-catalog-{pack.id}-v{manifest.catalog_version}.json"
    file_path = Path(assets_dir) / filename
    file_path.write_text(payload, encoding="utf-8")

    bare_path = f"companion-assets/{pack.user_id}/{filename}"
    return await publish_catalog(db, pack, manifest_path=bare_path, content_hash=content_hash)
