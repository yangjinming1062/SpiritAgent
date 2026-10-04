"""动作目录发布：manifest 构建、校验、CAS 版本推进与目录变更广播。位于 domains 层，供生成收尾、复核采纳、动作管理 API 与备份恢复（仅构建 manifest）共用；只依赖任务行与 pack 字段。"""

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from components import SETTINGS
from modules.companion import REQUIRED_SYSTEM_SLOTS, CompanionActionPack, PeekGeometry, parse_content_rect
from modules.ws import emit_ws_event
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import parse_companion_asset_path

from .asset_retirement import retire_action_assets
from .materials import accepted_action_asset
from .repository import StaleCatalogError, list_pack_actions, publish_catalog

_STORAGE_PATH_RE = re.compile(r"^companion-assets/\d+/[A-Za-z0-9._-]+$")
_PUBLISH_ATTEMPTS = 3


class _ActionClipSpec(BaseModel):
    """目录素材共同参数；动作语义不进入目录。"""

    model_config = ConfigDict(extra="forbid")

    media_type: Literal["image", "video"]
    action_id: int
    asset_revision: int = 1
    system_slot: str = ""
    media_ref: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    hitmask_ref: str | None = None
    hitmask_grid: tuple[int, int] | None = None
    peek_geometry: PeekGeometry | None = None
    content_rect: tuple[float, float, float, float] | None = None


class ActionImageSpec(_ActionClipSpec):
    media_type: Literal["image"] = "image"


class ActionVideoSpec(_ActionClipSpec):
    media_type: Literal["video"] = "video"
    duration_ms: int = Field(gt=0)
    frames: int = Field(gt=0)
    loopable: bool
    hitmask_fps: int = Field(gt=0, le=60)


ActionClipSpec = Annotated[ActionImageSpec | ActionVideoSpec, Field(discriminator="media_type")]


class ActionPackCanvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ActionCatalogManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: int
    outfit_id: int | None = None
    catalog_version: int = Field(gt=0)
    canvas: ActionPackCanvas
    clips: list[ActionClipSpec] = Field(min_length=1)
    cover_path: str | None = None
    default_action: str = "idle"


class CatalogValidationError(ValueError):
    """manifest 未通过发布校验；str 为公开文案。"""


def _require_pack_asset(path: str, user_id: int) -> None:
    """目录只引用该包所属用户的正式资产，客户端按此路径拉取素材。"""
    parsed = parse_companion_asset_path(path)
    if not _STORAGE_PATH_RE.fullmatch(path) or parsed is None or parsed[0] != user_id:
        raise CatalogValidationError(f"资源路径不合法：{path}")


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
    )

    actions = await list_pack_actions(db, pack.id, enabled_only=True, refresh=refresh_actions)
    clips: list[ActionClipSpec] = []
    slots_present: set[str] = set()
    cover_path = pack.cover_path

    for action in actions:
        material = accepted_action_asset(action)
        if material is None:
            continue

        peek_geometry = PeekGeometry.from_stored_json(material.peek_geometry_json)
        content_rect = parse_content_rect(material.content_rect_json)

        result_clip = material.parse_result().clip

        try:
            values = {
                "action_id": action.id,
                "asset_revision": material.metadata_revision,
                "system_slot": action.system_slot or "",
                "media_ref": material.media_path,
                "width": result_clip.width,
                "height": result_clip.height,
                "hitmask_ref": material.hitmask_path,
                "hitmask_grid": (
                    (material.hitmask_grid_w, material.hitmask_grid_h)
                    if material.hitmask_grid_w and material.hitmask_grid_h
                    else None
                ),
                "peek_geometry": peek_geometry,
                "content_rect": content_rect,
            }
            if material.media_type == "video":
                clip = ActionVideoSpec(
                    **values,
                    duration_ms=material.actual_duration_ms,
                    frames=material.frames,
                    loopable=material.loopable,
                    hitmask_fps=material.hitmask_fps,
                )
            else:
                clip = ActionImageSpec(**values)
        except ValidationError as exc:
            raise CatalogValidationError("动作素材参数不合法，请重新制作该动作") from exc
        clips.append(clip)
        if action.system_slot:
            slots_present.add(action.system_slot)
            if action.system_slot == "idle":
                cover_path = material.cover_path

    if any(slot not in slots_present for slot in REQUIRED_SYSTEM_SLOTS):
        return None
    for clip in clips:
        _require_pack_asset(clip.media_ref, pack.user_id)
        if clip.hitmask_ref is not None:
            _require_pack_asset(clip.hitmask_ref, pack.user_id)
    if cover_path is not None:
        _require_pack_asset(cover_path, pack.user_id)

    return ActionCatalogManifest(
        pack_id=pack.id,
        outfit_id=pack.outfit_id,
        catalog_version=pack.catalog_version + 1,
        canvas=canvas,
        clips=clips,
        cover_path=cover_path,
    )


async def publish_action_catalog(db: AsyncSession, pack: CompanionActionPack) -> int:
    """发布新目录快照：构建 manifest → 写入本次发布独有的文件 → CAS 推进版本指针。文件先于 CAS 写出且每次尝试路径唯一，落败方不会覆盖已发布版本的文件；本次文件只在 CAS 成功后交给目录，冲突、其他错误或取消时删除。CAS 落败时 flush 本事务改动后按数据库最新版本与动作行重建重试，仍冲突则抛 StaleCatalogError。失败只重试发布，不重新付费生成。"""
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
        try:
            file_path.write_text(payload, encoding="utf-8")
            previous = pack.manifest_path
            pack.cover_path = manifest.cover_path
            version = await publish_catalog(db, pack, manifest_path=manifest_path, content_hash=content_hash)
            await retire_action_assets(db, pack.user_id, [previous] if previous else [])
            return version
        except StaleCatalogError:
            file_path.unlink(missing_ok=True)
        except BaseException:
            file_path.unlink(missing_ok=True)
            raise
        await db.flush()
        await db.refresh(pack, attribute_names=["catalog_version", "manifest_path", "content_hash"])
    raise StaleCatalogError(f"pack {pack.id} catalog version kept advancing concurrently")


def emit_catalog_changed(db: AsyncSession, pack: CompanionActionPack) -> None:
    """广播包的当前目录版本与外观代次，在目录发布或激活之后调用；事件与调用方事务同写 outbox。"""
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.action.catalog_changed",
        payload={"packId": pack.id, "catalogVersion": pack.catalog_version, "appearanceEpoch": pack.appearance_epoch},
    )
