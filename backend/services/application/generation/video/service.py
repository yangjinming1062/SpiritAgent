"""视频动作包编排：外观版本 → 动作片段处理 → 不可变包发布 → 激活。

状态与阶段分离（status × stage）逐任务持久化；FFmpeg 等待走工作线程，
不占数据库长事务。发布与激活共用头像用户级锁：发布凭 reference_hash 拒绝迟到
结果，新包构建失败不清空旧激活包。首版仅开放「上传已制作片段 / 长视频区间导入」，
按参考生成入口待样包验收后启用（见 PIPELINE 视频章）。
"""

import asyncio
import contextlib
import hashlib
import tempfile
from pathlib import Path

from components import SESSION_LOCAL, get_logger, safe_json_loads
from modules.companion import (
    REQUIRED_VIDEO_ACTIONS,
    AvatarAsset,
    CompanionOutfit,
    CompanionVideoJob,
    CompanionVideoPack,
)
from modules.ws import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import get_or_create_persona
from services.infrastructure.assets import (
    save_companion_asset,
    signed_companion_asset_url,
    unlink_companion_asset,
)
from services.infrastructure.video_processing import (
    MAX_CANVAS_HEIGHT,
    MAX_CANVAS_WIDTH,
    MAX_SOURCE_BYTES,
    TARGET_EXT,
    VideoProcessError,
    build_hitmask,
    extract_cover,
    prepare_action_clip,
)
from services.infrastructure.video_processing.process import HITMASK_GRID_H, HITMASK_GRID_W

from ..avatar_service import get_avatar_job_lock
from .manifest import (
    MANIFEST_SCHEMA,
    ManifestValidationError,
    VideoClipSpec,
    VideoPackCanvas,
    VideoPackManifest,
    validate_pack_manifest,
)

logger = get_logger(__name__)

_DEFAULT_CANVAS = (512, 768)
_SOURCE_EXT_BY_MIME = {
    "video/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}
_BUILD_TASKS: set[asyncio.Task[None]] = set()


class VideoPackError(RuntimeError):
    """视频包流程错误；str 为公开文案。"""


class VideoPackNotFoundError(VideoPackError):
    """目标视频包不存在或不属于调用者。"""


class VideoPackStateError(VideoPackError):
    """状态守卫拒绝（未就绪 / 状态冲突 / 参考版本过期）。"""


def _reference_hash(outfit: CompanionOutfit | None, avatar: AvatarAsset | None) -> str:
    """参考版本指纹：外观立绘 + 参考立绘路径决定包归属；任一变化即视为新参考版本。"""
    payload = "|".join(
        (
            str(outfit.id) if outfit is not None else "",
            outfit.fullbody_url if outfit is not None else "",
            avatar.reference_image_url if avatar is not None else "",
        ),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_ext(content_type: str) -> str:
    ext = _SOURCE_EXT_BY_MIME.get(content_type.split(";", maxsplit=1)[0].strip().lower())
    if ext is None:
        raise VideoPackError("仅支持 WebM / MP4 / MOV / MKV 源片段")
    return ext


async def create_pack_from_clips(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int] = _DEFAULT_CANVAS,
    action_ranges: dict[str, tuple[float, float]] | None = None,
) -> CompanionVideoPack:
    """上传动作片段创建视频包：校验外观归属与片段后建 processing 包行，处理在后台进行。

    clips 为「动作键 → (源片段字节, MIME)」；action_ranges 提供长视频的每动作起止秒（可选）。
    源片段在处理开始时写入临时工作区；处理失败只影响本次包，不改变当前显示。"""
    if not clips:
        raise VideoPackError("请至少提供一个动作片段")
    missing = [action for action in REQUIRED_VIDEO_ACTIONS if action not in clips]
    if missing:
        raise VideoPackError("缺少必需动作片段：" + "、".join(missing))

    canvas_w, canvas_h = canvas
    if not (0 < canvas_w <= MAX_CANVAS_WIDTH and 0 < canvas_h <= MAX_CANVAS_HEIGHT):
        raise VideoPackError("画布尺寸超出限制")

    persona = await get_or_create_persona(db, user_id)
    if not persona.is_complete:
        raise VideoPackStateError("请先完成 onboarding 再创建视频形象")
    outfit = (
        await db.execute(
            select(CompanionOutfit).where(CompanionOutfit.id == outfit_id, CompanionOutfit.user_id == user_id),
        )
    ).scalar_one_or_none()
    if outfit is None:
        raise VideoPackError("找不到对应的外观")
    if outfit.status != "ready":
        raise VideoPackStateError("外观尚未确认，无法创建视频形象")
    avatar = (
        await db.execute(
            select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)),
        )
    ).scalar_one_or_none()
    reference_hash = _reference_hash(outfit, avatar)

    for action, (data, _content_type) in clips.items():
        if len(data) > MAX_SOURCE_BYTES:
            raise VideoPackError(f"{action} 片段超过大小上限")

    pack = CompanionVideoPack(
        user_id=user_id,
        avatar_id=avatar.id if avatar is not None else None,
        outfit_id=outfit.id,
        status="processing",
        reference_hash=reference_hash,
    )
    db.add(pack)
    await db.flush()
    # 版本号 = 同外观历史最大版本 + 1；版本不可覆盖，单动作重做即新版本。
    latest_version = (
        await db.execute(
            select(CompanionVideoPack.pack_version)
            .where(CompanionVideoPack.user_id == user_id, CompanionVideoPack.outfit_id == outfit.id)
            .order_by(CompanionVideoPack.pack_version.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    pack.pack_version = (latest_version or 0) + 1
    for action, (data, content_type) in clips.items():
        db.add(
            CompanionVideoJob(
                user_id=user_id,
                pack_id=pack.id,
                outfit_id=outfit.id,
                action=action,
                status="queued",
                stage="process",
                reference_hash=reference_hash,
                input_hash=hashlib.sha256(data).hexdigest(),
            ),
        )
    await db.commit()
    await db.refresh(pack)

    _kick_build(
        pack.id,
        clips=clips,
        canvas=(canvas_w, canvas_h),
        action_ranges=action_ranges or {},
        pack_version=pack.pack_version,
    )
    return pack


def _kick_build(
    pack_id: int,
    *,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int],
    action_ranges: dict[str, tuple[float, float]],
    pack_version: int,
) -> None:
    task = asyncio.create_task(
        _build_pack(
            pack_id,
            clips=clips,
            canvas=canvas,
            action_ranges=action_ranges,
            pack_version=pack_version,
        ),
        name=f"companion.video.build.{pack_id}",
    )
    _BUILD_TASKS.add(task)
    task.add_done_callback(_BUILD_TASKS.discard)


async def _build_pack(
    pack_id: int,
    *,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int],
    action_ranges: dict[str, tuple[float, float]],
    pack_version: int,
) -> None:
    """后台构建：逐动作处理 → manifest 校验 → 落库发布（ready 由处理结果决定，
    不依赖用户请求；激活才是显式用户动作）。"""
    canvas_w, canvas_h = canvas
    cover_path: str | None = None
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionVideoPack, pack_id)
            if pack is None:
                return
            user_id = pack.user_id
            outfit_id = pack.outfit_id

        clip_specs: list[VideoClipSpec] = []
        with tempfile.TemporaryDirectory(prefix="video-pack-") as tmp:
            work = Path(tmp)
            for action, (data, content_type) in clips.items():
                src = work / f"{action}_src{_source_ext(content_type)}"
                src.write_bytes(data)
                dst = work / f"{action}.{TARGET_EXT}"
                start, end = action_ranges.get(action, (None, None))
                result = await asyncio.to_thread(
                    prepare_action_clip,
                    src,
                    dst,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    start_seconds=start,
                    end_seconds=end,
                )
                stored = save_companion_asset(
                    dst.read_bytes(),
                    user_id=user_id,
                    label=f"video_{action}",
                    ext="webm",
                )
                hitmask = await asyncio.to_thread(build_hitmask, dst)
                cover = dst.with_suffix(".webp")
                await asyncio.to_thread(extract_cover, dst, cover)
                cover_stored = save_companion_asset(
                    cover.read_bytes(),
                    user_id=user_id,
                    label=f"video_{action}_cover",
                    ext="webp",
                )
                if action == "idle":
                    cover_path = cover_stored
                clip_specs.append(
                    VideoClipSpec(
                        action=action,
                        path=stored,
                        sha256=result.sha256,
                        bytes=result.bytes,
                        frames=result.frames,
                        duration_ms=result.duration_ms,
                        loop=True,
                        hitmask=hitmask,
                        hitmask_grid=(HITMASK_GRID_W, HITMASK_GRID_H),
                    ),
                )

            manifest = VideoPackManifest(
                schema_version=MANIFEST_SCHEMA,
                pack_id=pack_id,
                appearance_id=outfit_id,
                pack_version=pack_version,
                canvas=VideoPackCanvas(width=canvas_w, height=canvas_h, fps=24),
                clips=clip_specs,
                cover_path=cover_path,
            )
            validate_pack_manifest(manifest)
            manifest_bytes = manifest.model_dump_json(indent=1).encode("utf-8")

        content_hash = hashlib.sha256(manifest_bytes).hexdigest()
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionVideoPack, pack_id)
            if pack is None or pack.status != "processing":
                return
            manifest_path = save_companion_asset(
                manifest_bytes,
                user_id=pack.user_id,
                label=f"video_pack_manifest_{pack.id}",
                ext="json",
            )
            pack.manifest_json = manifest_bytes.decode("utf-8")
            pack.manifest_path = manifest_path
            pack.content_hash = content_hash
            pack.status = "ready"
            pack.error = None
            await _mark_jobs(db, pack.id, status="succeeded", stage="publish")
            emit_ws_event(
                db,
                user_id=pack.user_id,
                event_type="companion.video.ready",
                payload={"packId": pack.id, "outfitId": pack.outfit_id, "packVersion": pack.pack_version},
            )
            await db.commit()
    except (VideoProcessError, ManifestValidationError, VideoPackError, OSError) as exc:
        await _fail_pack(pack_id, exc)
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底：失败必须落库可见
        logger.exception("video pack build crashed")
        await _fail_pack(pack_id, exc)


async def _mark_jobs(
    db: AsyncSession,
    pack_id: int,
    *,
    status: str,
    stage: str,
    error: str | None = None,
) -> None:
    await db.execute(
        update(CompanionVideoJob)
        .where(CompanionVideoJob.pack_id == pack_id)
        .values(status=status, stage=stage, error=error)
        .execution_options(synchronize_session=False),
    )


async def _fail_pack(pack_id: int, exc: Exception) -> None:
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None or pack.status != "processing":
            return
        pack.status = "failed"
        pack.error = str(exc)[:500]
        await _mark_jobs(db, pack.id, status="failed", stage="process", error=str(exc)[:500])
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.video.failed",
            payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": str(exc)[:300]},
        )
        await db.commit()


async def activate_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionVideoPack:
    """激活就绪视频包：同事务翻转唯一激活；迟到或未就绪的包不得抢回显示。"""
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError(f"video pack {pack_id} not found")
        if pack.status != "ready":
            raise VideoPackStateError("视频包尚未就绪，无法启用")
        await db.execute(
            update(CompanionVideoPack)
            .where(CompanionVideoPack.user_id == user_id, CompanionVideoPack.active.is_(True))
            .values(active=False)
            .execution_options(synchronize_session=False),
        )
        pack.active = True
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.video.activated",
            payload={"packId": pack.id, "packVersion": pack.pack_version, "outfitId": pack.outfit_id},
        )
        await db.commit()
        await db.refresh(pack)
    return pack


async def delete_pack(db: AsyncSession, user_id: int, pack_id: int) -> None:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError(f"video pack {pack_id} not found")
        if pack.active:
            raise VideoPackStateError("使用中的视频包不能删除，请先启用其他形象")
        manifest = safe_json_loads(pack.manifest_json or "{}", default={})
        if isinstance(manifest, dict):
            for clip in manifest.get("clips", []) or []:
                path = clip.get("path") if isinstance(clip, dict) else None
                if isinstance(path, str) and path:
                    with contextlib.suppress(OSError):
                        unlink_companion_asset(path)
        with contextlib.suppress(OSError):
            unlink_companion_asset(pack.manifest_path)
        await db.delete(pack)
        await db.commit()


async def _get_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionVideoPack | None:
    return (
        await db.execute(
            select(CompanionVideoPack).where(CompanionVideoPack.id == pack_id, CompanionVideoPack.user_id == user_id),
        )
    ).scalar_one_or_none()


def pack_response(pack: CompanionVideoPack) -> dict:
    """视频包行转响应；manifest URL 现签，片段资源 URL 由客户端按 manifest 拉取。"""
    return {
        "id": pack.id,
        "outfit_id": pack.outfit_id,
        "pack_version": pack.pack_version,
        "status": pack.status,
        "active": pack.active,
        "content_hash": pack.content_hash or None,
        "manifest_url": signed_companion_asset_url(pack.manifest_path) if pack.status == "ready" else None,
        "error": pack.error or None,
    }


async def list_pack_responses(db: AsyncSession, user_id: int) -> list[dict]:
    """用户的视频包列表（按创建时间倒序）；manifest URL 现签。"""
    packs = (
        (
            await db.execute(
                select(CompanionVideoPack)
                .where(CompanionVideoPack.user_id == user_id)
                .order_by(CompanionVideoPack.created_at.desc()),
            )
        )
        .scalars()
        .all()
    )
    return [pack_response(pack) for pack in packs]


async def resume_processing_packs() -> None:
    """进程重启恢复：processing 包的构建任务随进程丢失，按失败落库并广播；
    不自动重做处理（源片段在临时工作区，重启后不可复用），用户显式重交即可。"""
    async with SESSION_LOCAL() as db:
        packs = (
            (await db.execute(select(CompanionVideoPack).where(CompanionVideoPack.status == "processing")))
            .scalars()
            .all()
        )
        for pack in packs:
            pack.status = "failed"
            pack.error = "处理进程重启，请重新提交"
            await _mark_jobs(db, pack.id, status="failed", stage="process", error="process restarted")
            emit_ws_event(
                db,
                user_id=pack.user_id,
                event_type="companion.video.failed",
                payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": "process restarted"},
            )
        if packs:
            await db.commit()
            logger.info("resumed video packs marked failed", extra={"count": len(packs)})
