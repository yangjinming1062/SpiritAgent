"""动作制作：helpers。"""

import asyncio
import contextlib
import json
from collections.abc import Callable
from pathlib import Path

from infrastructure.assets.asset_store import (
    build_data_uri,
    image_mime_for_extension,
    pack_asset_directory,
    save_companion_asset_async,
    sniff_media_ext,
)
from infrastructure.config import SETTINGS
from infrastructure.video_processing.frames import action_frame_video_input
from infrastructure.video_processing.process import HITMASK_FPS, HITMASK_GRID_H, HITMASK_GRID_W, sample_key_frames

from features.actions.media_contracts import ActionResult, ImageClipSpec, VideoClipSpec
from features.actions.models import CompanionActionPack, make_action_reference_hash
from features.appearance.avatar_service import load_avatar_bytes_as_data_uri, read_portrait_bytes
from features.appearance.query_contracts import AvatarSnapshot, OutfitSnapshot

from .errors import VideoPackError
from .execution_state import _SOURCE_EXT_BY_MIME


async def _process_thread[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


def _pack_dir(pack: CompanionActionPack) -> str:
    return pack_asset_directory(pack.outfit_id, pack.id)


async def _reference_hash(outfit: OutfitSnapshot | None, avatar: AvatarSnapshot | None) -> str:
    """参考版本指纹包含外观 ID、身份 ID 与参考图实际字节，阻断迟到结果。"""
    reference = await _process_thread(load_avatar_bytes_as_data_uri, outfit.fullbody_url) if outfit is not None else ""
    return make_action_reference_hash(
        outfit.id if outfit is not None else None,
        outfit.fullbody_url if outfit is not None else "",
        avatar.id if avatar is not None else None,
        reference or "",
    )


async def _copy_portrait_asset(path: str, *, user_id: int, label: str, directory: str) -> str | None:
    """把头像或外观图复制为本任务独有的冻结资产；源不可读时返回 None。"""
    loaded = await _process_thread(read_portrait_bytes, path)
    if loaded is None:
        return None
    data = loaded[0]
    return await save_companion_asset_async(
        data,
        user_id=user_id,
        label=label,
        ext=sniff_media_ext(data) or "png",
        directory=directory,
    )


def _source_ext(content_type: str) -> str:
    ext = _SOURCE_EXT_BY_MIME.get(content_type.split(";", maxsplit=1)[0].strip().lower())
    if ext is None:
        raise VideoPackError("仅支持 WebM / MP4 / MOV / MKV 源片段")
    return ext


def _image_data_uri(path: Path) -> str:
    data = path.read_bytes()
    mime = image_mime_for_extension(sniff_media_ext(data) or "")
    if mime is None:
        raise VideoPackError("参考图格式无效")
    return build_data_uri(data, mime)


def _action_video_frame_uri(path: Path) -> str:
    data = action_frame_video_input(path.read_bytes())
    return build_data_uri(data, "image/png")


def _artifact_abs_path(stored: str) -> Path:
    return Path(SETTINGS.data_dir) / stored


async def _clip_frame_uris(clip: VideoClipSpec) -> tuple[str, ...]:
    """交付片段的首、中、末帧 data URI，供身份评分与复核。"""
    frames = await _process_thread(sample_key_frames, _artifact_abs_path(clip.path))
    return tuple(build_data_uri(frame, "image/webp") for frame in frames)


async def _action_sample_uris(clip: ImageClipSpec | VideoClipSpec) -> tuple[str, ...]:
    if clip.media_type == "image":
        return (await _process_thread(_image_data_uri, _artifact_abs_path(clip.path)),)
    return await _clip_frame_uris(clip)


def _hitmask_content_rect(hitmask: list[list[int]]) -> tuple[float, float, float, float] | None:
    occupied_columns = 0
    top = HITMASK_GRID_H
    bottom = 0
    column_mask = (1 << HITMASK_GRID_W) - 1
    for frame in hitmask:
        for y, row in enumerate(frame):
            row &= column_mask
            if row:
                occupied_columns |= row
                top = min(top, y)
                bottom = max(bottom, y + 1)
    if not occupied_columns:
        return None
    columns = [x for x in range(HITMASK_GRID_W) if occupied_columns & (1 << x)]
    return (
        columns[0] / HITMASK_GRID_W,
        top / HITMASK_GRID_H,
        (columns[-1] + 1) / HITMASK_GRID_W,
        bottom / HITMASK_GRID_H,
    )


def _clip_row_values(result: ActionResult) -> dict[str, object]:
    """本次制作的素材列；发布只读取已采纳快照。"""
    clip = result.clip
    return {
        "media_type": clip.media_type,
        "result_json": result.model_dump_json(),
        "media_path": clip.path,
        "media_hash": clip.sha256,
        "actual_duration_ms": clip.duration_ms if clip.media_type == "video" else None,
        "frames": clip.frames if clip.media_type == "video" else None,
        "peek_geometry_json": clip.peek_geometry.model_dump_json() if clip.peek_geometry else None,
        "content_rect_json": json.dumps(clip.content_rect) if clip.content_rect else None,
        "cover_path": result.cover_path,
        "hitmask_path": result.hitmask_path,
        "hitmask_grid_w": HITMASK_GRID_W,
        "hitmask_grid_h": HITMASK_GRID_H,
        "hitmask_fps": HITMASK_FPS if clip.media_type == "video" else None,
    }
