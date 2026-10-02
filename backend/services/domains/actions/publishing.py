"""动作目录发布：manifest 构建、校验、CAS 版本推进与目录变更广播。位于 domains 层，供生成收尾、复核采纳、动作管理 API 与备份恢复（仅构建 manifest）共用；只依赖任务行与 pack 字段。"""

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from components import SETTINGS, safe_json_loads
from modules.companion import REQUIRED_SYSTEM_SLOTS, CompanionActionPack, PeekGeometry, parse_content_rect
from modules.ws import emit_ws_event
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import parse_companion_asset_path

from .repository import StaleCatalogError, list_pack_actions, publish_catalog

MANIFEST_SCHEMA = "spiritagent.action.pack"

_STORAGE_PATH_RE = re.compile(r"^companion-assets/\d+/[A-Za-z0-9._-]+$")
_PUBLISH_ATTEMPTS = 3


class ActionClipSpec(BaseModel):
    """目录 clip：可验证素材与播放技术参数；动作语义（运动描述、适用与避免条件）不进入目录。"""

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
    outfit_id: int | None = None
    catalog_version: int = Field(gt=0)
    canvas: ActionPackCanvas
    clips: list[ActionClipSpec] = Field(min_length=1)
    cover_path: str | None = None
    default_action: str = "idle"


class CatalogValidationError(ValueError):
    """manifest 未通过发布校验；str 为公开文案。"""


def _clip_size(result_json: str | None) -> tuple[int, int]:
    """处理结果中的片段像素尺寸；缺失或非法时拒绝发布。"""
    result = safe_json_loads(result_json or "", default=None)
    clip = result.get("clip") if isinstance(result, dict) else None
    width, height = (clip.get("width"), clip.get("height")) if isinstance(clip, dict) else (None, None)
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise CatalogValidationError("动作素材缺少有效的片段尺寸，请重新制作该动作")
    return width, height


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
        width, height = _clip_size(action.result_json)

        try:
            clip = ActionClipSpec(
                action_id=action.id,
                asset_revision=action.metadata_revision,
                system_slot=action.system_slot or "",
                video_ref=action.video_path,
                duration_ms=max(duration_ms, 1),
                frames=max(frames, 1),
                width=width,
                height=height,
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
            )
        except ValidationError as exc:
            raise CatalogValidationError("动作素材参数不合法，请重新制作该动作") from exc
        clips.append(clip)
        if action.system_slot:
            slots_present.add(action.system_slot)

    if any(slot not in slots_present for slot in REQUIRED_SYSTEM_SLOTS):
        return None
    for clip in clips:
        _require_pack_asset(clip.video_ref, pack.user_id)
        if clip.hitmask_ref is not None:
            _require_pack_asset(clip.hitmask_ref, pack.user_id)
    if pack.cover_path is not None:
        _require_pack_asset(pack.cover_path, pack.user_id)

    return ActionCatalogManifest(
        pack_id=pack.id,
        outfit_id=pack.outfit_id,
        catalog_version=pack.catalog_version + 1,
        canvas=canvas,
        clips=clips,
        cover_path=pack.cover_path,
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
            return await publish_catalog(db, pack, manifest_path=manifest_path, content_hash=content_hash)
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
