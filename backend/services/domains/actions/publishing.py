"""动作目录发布：manifest 构建、校验与 CAS 版本推进。位于 domains 层供 generation 收尾与 application/actions 共用；只依赖任务行与 pack 字段。"""

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from components import SETTINGS
from modules.companion import REQUIRED_SYSTEM_SLOTS, CompanionActionPack, PeekGeometry, parse_content_rect
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .repository import StaleCatalogError, list_pack_actions, publish_catalog

MANIFEST_SCHEMA = "spiritagent.action.pack"

_STORAGE_PATH_RE = re.compile(r"^companion-assets/\d+/[A-Za-z0-9._-]+$")
_PUBLISH_ATTEMPTS = 3


class ActionClipSpec(BaseModel):
    """目录 clip：可验证素材与播放技术参数；使用场景元数据走 catalog API。"""

    model_config = ConfigDict(extra="forbid")

    action_id: int
    asset_revision: int = 1
    system_slot: str = ""
    video_ref: str
    duration_ms: int = Field(gt=0)
    frames: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    loopable: bool = False
    # 目录契约字段，后端不产出进出姿态，恒为 null。
    enter_pose: None = None
    exit_pose: None = None
    hitmask_ref: str | None = None
    hitmask_grid: tuple[int, int] | None = None
    hitmask_fps: int = Field(gt=0, le=60)
    peek_geometry: PeekGeometry | None = None
    content_rect: tuple[float, float, float, float] | None = None


class ActionPackCanvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0, le=60)


class ActionCatalogManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = MANIFEST_SCHEMA
    pack_id: int
    # 目录契约字段，包不绑定独立角色 ID，恒为 null。
    character_id: None = None
    outfit_id: int | None = None
    catalog_version: int = Field(gt=0)
    canvas: ActionPackCanvas
    clips: list[ActionClipSpec] = Field(min_length=1)
    cover_path: str | None = None
    default_action: str = "idle"


class CatalogValidationError(ValueError):
    """manifest 未通过发布校验；str 为公开文案。"""


async def build_catalog_manifest(
    db: AsyncSession,
    pack: CompanionActionPack,
    *,
    refresh_actions: bool = False,
) -> ActionCatalogManifest | None:
    """合并当前成功动作构建新 manifest；必需槽位不齐返回 None（不发布，不破坏现有目录）。系统槽位在包内由唯一索引保证不重复；refresh_actions 语义同 `list_pack_actions` 的 refresh。"""
    canvas_data = json.loads(pack.canvas_spec or "{}")
    canvas = ActionPackCanvas(
        width=canvas_data.get("width", 512),
        height=canvas_data.get("height", 512),
        fps=canvas_data.get("fps", 24),
    )

    actions = await list_pack_actions(db, pack.id, enabled_only=True, refresh=refresh_actions)
    clips: list[ActionClipSpec] = []
    slots_present: set[str] = set()

    for action in actions:
        if action.status != "succeeded" or not action.video_path:
            continue

        peek_geometry = PeekGeometry.from_stored_json(action.peek_geometry_json)
        content_rect = parse_content_rect(action.content_rect_json)

        duration_ms = action.actual_duration_ms or int(action.target_duration_seconds * 1000) or 2000
        frames = action.frames or int(action.target_duration_seconds * 24) or 48
        if not action.result_json:
            raise CatalogValidationError("动作素材缺少处理结果")
        clip_data = json.loads(action.result_json)["clip"]

        clips.append(
            ActionClipSpec(
                action_id=action.id,
                asset_revision=action.metadata_revision,
                system_slot=action.system_slot or "",
                video_ref=action.video_path,
                duration_ms=max(duration_ms, 1),
                frames=max(frames, 1),
                width=clip_data["width"],
                height=clip_data["height"],
                loopable=action.loopable,
                hitmask_ref=action.hitmask_path,
                hitmask_grid=(
                    (action.hitmask_grid_w, action.hitmask_grid_h)
                    if action.hitmask_grid_w and action.hitmask_grid_h
                    else None
                ),
                hitmask_fps=action.hitmask_fps or 24,
                peek_geometry=peek_geometry,
                content_rect=content_rect,
            ),
        )
        if action.system_slot:
            slots_present.add(action.system_slot)

    if any(slot not in slots_present for slot in REQUIRED_SYSTEM_SLOTS):
        return None
    for clip in clips:
        if not _STORAGE_PATH_RE.match(clip.video_ref):
            raise CatalogValidationError(f"资源路径不合法：{clip.video_ref}")

    return ActionCatalogManifest(
        pack_id=pack.id,
        outfit_id=pack.outfit_id,
        catalog_version=pack.catalog_version + 1,
        canvas=canvas,
        clips=clips,
        cover_path=pack.cover_path,
    )


async def publish_action_catalog(db: AsyncSession, pack: CompanionActionPack) -> int:
    """发布新目录快照：构建 manifest → 写入本次发布独有的文件 → CAS 推进版本指针。文件先于 CAS 写出且每次尝试路径唯一，落败方不会覆盖已发布版本的文件；CAS 落败时删除本次文件，flush 本事务改动后按数据库最新版本与动作行重建重试，仍冲突则抛 StaleCatalogError。失败只重试发布，不重新付费生成。"""
    for attempt in range(_PUBLISH_ATTEMPTS):
        manifest = await build_catalog_manifest(db, pack, refresh_actions=attempt > 0)
        if manifest is None:
            raise CatalogValidationError("当前成功动作不足以发布目录")

        payload = manifest.model_dump_json()
        content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        filename = f"action-catalog-{pack.id}-v{manifest.catalog_version}-{uuid4().hex}.json"
        manifest_path = f"companion-assets/{pack.user_id}/{filename}"
        file_path = Path(SETTINGS.data_dir) / manifest_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(payload, encoding="utf-8")
        try:
            return await publish_catalog(db, pack, manifest_path=manifest_path, content_hash=content_hash)
        except StaleCatalogError:
            file_path.unlink(missing_ok=True)
            await db.flush()
            await db.refresh(pack, attribute_names=["catalog_version", "manifest_path", "content_hash"])
    raise StaleCatalogError(f"pack {pack.id} catalog version kept advancing concurrently")
