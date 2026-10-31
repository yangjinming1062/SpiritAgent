"""用户影像原件的检查、去重与事务发布；不调用生成或生活执行。"""

import hashlib
import io
import math
from uuid import uuid4

from infrastructure.assets.asset_store import resolve_asset_reference, save_companion_asset, sniff_media_ext
from infrastructure.assets.write_protection import asset_write_context, user_asset_lock
from infrastructure.async_io import run_blocking
from infrastructure.config import SETTINGS
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.database import SESSION_LOCAL
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import or_, select

from features.assets.lifecycle import cleanup_asset_directories_after_task
from features.jobs.service import bind_job_assets

from .models import VisualImportJob, VisualItem, VisualVersion
from .publication import publish_imported_visual
from .queries import visual_detail
from .repository import VisualStateError, notify_visuals_updated, require_presentation_row
from .schemas import (
    VISUAL_MAX_EDGE,
    AnalyzingVisualSource,
    VisualAsset,
    VisualDetailView,
    VisualSnapshot,
)


def _image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise VisualStateError("请选择 PNG、JPEG、WebP 或 GIF 图片")
        frames = getattr(image, "n_frames", 1)
        if max(image.size) > VISUAL_MAX_EDGE or frames > 500 or image.width * image.height * frames > 150_000_000:
            raise VisualStateError("图片尺寸或动图帧数过大")
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        # 保留动图原件；逐帧解码防止截断文件在收藏成功后才暴露损坏。
        for frame in range(getattr(image, "n_frames", 1)):
            image.seek(frame)
            image.load()
        return ImageOps.exif_transpose(image).size


async def import_visual(
    user_id: int,
    data: bytes,
    *,
    filename: str,
    title: str,
    description: str,
) -> VisualDetailView:
    ext = sniff_media_ext(data)
    image = ext in {"png", "jpg", "webp", "gif"}
    if not data or (not image and ext not in {"mp4", "mov", "webm"}):
        raise VisualStateError("请选择 PNG、JPEG、WebP、GIF 图片或 MP4、MOV、WebM 视频")
    limit = REMOTE_ASSET_DOWNLOAD_MAX_BYTES if image else SETTINGS.video_attachment_max_bytes
    if len(data) > limit:
        raise VisualStateError(f"影像超过 {limit // (1024 * 1024)} MB 上限")
    digest = hashlib.sha256(data).hexdigest()
    directory = f"visuals/imports/{uuid4().hex}"
    try:
        async with user_asset_lock(user_id), SESSION_LOCAL() as db:
            # 与制作、恢复共用账户串行边界，数据库行锁覆盖并发上传。
            await require_presentation_row(db, user_id, lock=True)
            existing = (
                await db.execute(
                    select(
                        VisualItem.id,
                        VisualVersion.snapshot_json["source"]["original_path"].astext.label("original_path"),
                        VisualVersion.media_path,
                    )
                    .select_from(VisualItem)
                    .outerjoin(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
                    .where(
                        VisualItem.user_id == user_id,
                        or_(
                            VisualItem.content_hash == digest,
                            VisualItem.id.in_(
                                select(VisualImportJob.visual_id).where(
                                    VisualImportJob.user_id == user_id, VisualImportJob.original_hash == digest
                                )
                            ),
                        ),
                    )
                )
            ).first()
            await db.commit()
            if existing is not None:
                visual_id, original_path, media_path = existing
                if resolve_asset_reference(original_path or media_path) is None:
                    raise VisualStateError("同内容影像的原件已缺失，请先恢复原件")
                return await visual_detail(user_id, visual_id)
            with asset_write_context():
                path = await run_blocking(
                    save_companion_asset, data, user_id=user_id, label="original", ext=ext, directory=directory
                )
                if image:
                    width, height = await run_blocking(_image_size, data)
                    poster_path, duration_ms = path, None
                else:
                    resolved = resolve_asset_reference(path)
                    if resolved is None:
                        raise VisualStateError("上传原件保存失败")
                    probe = await run_blocking(probe_video, resolved[0])
                    if not math.isfinite(probe.duration_seconds):
                        raise VisualStateError("视频时长无效")
                    if max(probe.width, probe.height) > 8192 or probe.width * probe.height > 40_000_000:
                        raise VisualStateError("视频分辨率过大，请使用不超过 8K 的视频")
                    poster = await run_blocking(extract_desktop_first_frame, resolved[0])
                    width, height = await run_blocking(_image_size, poster)
                    duration_ms = max(1, round(probe.duration_seconds * 1000))
                    poster_path = await run_blocking(
                        save_companion_asset, poster, user_id=user_id, label="poster", ext="png", directory=directory
                    )
                snapshot = VisualSnapshot(
                    source=AnalyzingVisualSource(
                        original_filename=filename,
                        original_path=path,
                        original_content_hash=digest,
                        original_width=width,
                        original_height=height,
                        original_duration_ms=duration_ms,
                    ),
                    target_width=width,
                    target_height=height,
                )
                asset = VisualAsset(
                    media_kind="image" if image else "video",
                    media_path=path,
                    poster_path=poster_path,
                    width=width,
                    height=height,
                    duration_ms=duration_ms,
                    visual_description=description,
                )
                await require_presentation_row(db, user_id, lock=True)
                # 探测期间不持事务；提交前重查其他请求已经发布的相同内容。
                existing = (
                    await db.execute(
                        select(
                            VisualItem.id,
                            VisualVersion.snapshot_json["source"]["original_path"].astext.label("original_path"),
                            VisualVersion.media_path,
                        )
                        .select_from(VisualItem)
                        .outerjoin(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
                        .where(
                            VisualItem.user_id == user_id,
                            or_(
                                VisualItem.content_hash == digest,
                                VisualItem.id.in_(
                                    select(VisualImportJob.visual_id).where(
                                        VisualImportJob.user_id == user_id, VisualImportJob.original_hash == digest
                                    )
                                ),
                            ),
                        )
                    )
                ).first()
                if existing is not None:
                    visual_id, original_path, media_path = existing
                    if resolve_asset_reference(original_path or media_path) is None:
                        raise VisualStateError("同内容影像的原件已缺失，请先恢复原件")
                else:
                    visual_id = await publish_imported_visual(db, user_id, asset, snapshot, title=title)
                    # 原件与分析任务在同一个事务登记，避免进程在两步之间退出后留下孤立影像。
                    job = VisualImportJob(
                        user_id=user_id,
                        visual_id=visual_id,
                        expected_version=1,
                        original_hash=digest,
                        state="queued",
                        stage="analyze",
                    )
                    db.add(job)
                    await db.flush()
                    await bind_job_assets(db, user_id, job.id, {path}, "original")
                await notify_visuals_updated(db, user_id)
                await db.commit()
        return await visual_detail(user_id, visual_id)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, VideoProcessError) as exc:
        raise VisualStateError(f"无法读取影像：{exc}") from exc
    finally:
        # 请求取消、发布失败与重复上传也回收未引用文件；已提交版本由统一资产保护保留。
        cleanup_asset_directories_after_task(user_id, [directory])
