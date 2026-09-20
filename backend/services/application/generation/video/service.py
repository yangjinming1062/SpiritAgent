"""视频动作包编排：外观版本 → 动作片段处理 → 不可变包发布 → 激活。

状态与阶段分离（status × stage）逐任务持久化；FFmpeg 等待走工作线程，
不占数据库长事务。发布与激活共用头像用户级锁：发布凭 reference_hash 拒绝迟到
结果，新包构建失败不清空旧激活包。

两条创建入口：
- 上传导入：`create_pack_from_clips`，用户提供已制作片段（或长视频区间）；
- 按参考生成：`create_pack_from_reference`，LLM 演绎脚本 → 独立动作姿态 →
  Grok 首尾帧短片 → 语义抠像与循环验收 → 不可变包发布。
  供应商任务句柄在提交后立即落库，重启后凭句柄续轮询，不重复提交付费任务。
"""

import asyncio
import base64
import contextlib
import hashlib
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from components import (
    SESSION_LOCAL,
    SETTINGS,
    backoff_for_poll,
    download_capped,
    get_logger,
    safe_json_loads,
    utc_now,
)
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
from sqlalchemy.orm.attributes import flag_modified

from services.domains.companion import get_or_create_persona, load_persona_definition
from services.infrastructure.assets import (
    save_companion_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
    unlink_companion_asset,
)
from services.infrastructure.llm import (
    ProviderConfig,
    ProviderError,
    ProviderResultUnknownError,
    ServiceType,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
    execute_with_fallback,
    resolve,
    resolve_provider_chain,
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
from services.infrastructure.video_processing.matting import matte_video, require_matting_model
from services.infrastructure.video_processing.process import HITMASK_FPS, HITMASK_GRID_H, HITMASK_GRID_W
from services.infrastructure.video_processing.quality import select_loop

from ..avatar_service import get_avatar_job_lock, load_avatar_bytes_as_data_uri
from ..image_generation import ImageGenerationError, generate_images, resolve_image_gen_chain
from .manifest import (
    MANIFEST_SCHEMA,
    ManifestValidationError,
    VideoClipSpec,
    VideoPackCanvas,
    VideoPackManifest,
    validate_pack_manifest,
)
from .script import (
    ACTION_DURATIONS,
    ActionScriptEntry,
    VideoScriptError,
    build_pose_prompt,
    build_video_prompt,
    compose_action_script,
)
from .state import ActionResult, GenerationContext

logger = get_logger(__name__)

_DEFAULT_CANVAS = (512, 768)
_SOURCE_EXT_BY_MIME = {
    "video/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}
# 生成源接受的视频容器（供应商产物与内部中间产物）
_SOURCE_MEDIA_EXTS = {"mp4", "webm", "mov", "mkv"}

_BUILD_TASKS: set[asyncio.Task[None]] = set()
_GEN_TASKS: set[asyncio.Task[None]] = set()
_GEN_INFLIGHT: set[int] = set()


async def _process_thread[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


class VideoPackError(RuntimeError):
    """视频包流程错误；str 为公开文案，internal 保留定位线索。"""

    def __init__(self, message: str, *, internal: str | None = None) -> None:
        super().__init__(message)
        self.internal = internal


class VideoPackNotFoundError(VideoPackError):
    """目标视频包不存在或不属于调用者。"""


class VideoPackStateError(VideoPackError):
    """状态守卫拒绝（未就绪 / 状态冲突 / 参考版本过期）。"""


async def _reference_hash(outfit: CompanionOutfit | None, avatar: AvatarAsset | None) -> str:
    """参考版本指纹包含外观 ID、身份 ID 与参考图实际字节，阻断迟到结果。"""
    reference = await _process_thread(load_avatar_bytes_as_data_uri, outfit.fullbody_url) if outfit is not None else ""
    payload = "|".join(
        (
            str(outfit.id) if outfit is not None else "",
            outfit.fullbody_url if outfit is not None else "",
            str(avatar.id) if avatar is not None else "",
            reference or "",
        ),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_ext(content_type: str) -> str:
    ext = _SOURCE_EXT_BY_MIME.get(content_type.split(";", maxsplit=1)[0].strip().lower())
    if ext is None:
        raise VideoPackError("仅支持 WebM / MP4 / MOV / MKV 源片段")
    return ext


def _image_data_uri(path: Path) -> str:
    data = path.read_bytes()
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(sniff_media_ext(data) or "")
    if mime is None:
        raise VideoPackError("参考图格式无效")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _artifact_abs_path(stored: str) -> Path:
    return Path(SETTINGS.data_dir) / stored


async def _reject_concurrent_build(db: AsyncSession, user_id: int) -> None:
    """同一用户同时只允许一个构建中的视频包，避免发布与激活竞态。"""
    processing = (
        await db.execute(
            select(CompanionVideoPack.id).where(
                CompanionVideoPack.user_id == user_id,
                CompanionVideoPack.status == "processing",
            ),
        )
    ).scalar_one_or_none()
    if processing is not None:
        raise VideoPackStateError("已有视频形象任务进行中，请等待完成后再试")


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
    if set(clips) != set(REQUIRED_VIDEO_ACTIONS):
        raise VideoPackError("请提供待机、左右行走与拖拽四个独立动作")
    canvas_w, canvas_h = canvas
    if not (0 < canvas_w <= MAX_CANVAS_WIDTH and 0 < canvas_h <= MAX_CANVAS_HEIGHT) or canvas_w % 2 or canvas_h % 2:
        raise VideoPackError("画布尺寸超出限制")

    async with get_avatar_job_lock(user_id):
        persona = await get_or_create_persona(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成 onboarding 再创建视频形象")
        await _reject_concurrent_build(db, user_id)
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
        reference_hash = await _reference_hash(outfit, avatar)

        for action, (data, _content_type) in clips.items():
            if len(data) > MAX_SOURCE_BYTES:
                raise VideoPackError(f"{action} 片段超过大小上限")

        pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
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
    )
    return pack


async def create_pack_from_reference(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int | None = None,
    force: bool = False,
    source_pack_id: int | None = None,
    action: str | None = None,
    feedback: str = "",
) -> CompanionVideoPack:
    """独立 Grok 短动作；单动作重做形成新版本，复用同一冻结参考及其他成功动作。"""
    if (source_pack_id is None) != (action is None) or (action is not None and action not in REQUIRED_VIDEO_ACTIONS):
        raise VideoPackStateError("单动作重做必须指定有效视频包与动作")
    async with get_avatar_job_lock(user_id):
        persona = await get_or_create_persona(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成 onboarding")
        await _reject_concurrent_build(db, user_id)
        source = await _get_pack(db, user_id, source_pack_id) if source_pack_id is not None else None
        if source_pack_id is not None and (
            source is None or source.status not in ("ready", "failed") or not source.reference_path
        ):
            raise VideoPackStateError("该视频包不能重做动作")
        if source is not None:
            outfit_id = source.outfit_id
        query = select(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.status == "ready")
        query = (
            query.where(CompanionOutfit.id == outfit_id)
            if outfit_id is not None
            else query.where(CompanionOutfit.active.is_(True))
        )
        outfit = (await db.execute(query)).scalar_one_or_none()
        if outfit is None:
            raise VideoPackStateError("请先确认外观参考图")
        avatar = (
            await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
        ).scalar_one_or_none()
        reference_hash = await _reference_hash(outfit, avatar)
        if source is not None and source.reference_hash != reference_hash:
            raise VideoPackStateError("外观参考已变更，请生成完整新包")
        if not force and source is None:
            reusable = (
                await db.execute(
                    select(CompanionVideoPack)
                    .where(
                        CompanionVideoPack.user_id == user_id,
                        CompanionVideoPack.outfit_id == outfit.id,
                        CompanionVideoPack.reference_hash == reference_hash,
                        CompanionVideoPack.status == "ready",
                    )
                    .order_by(CompanionVideoPack.pack_version.desc())
                    .limit(1),
                )
            ).scalar_one_or_none()
            if reusable is not None:
                await _activate_locked(db, reusable)
                await db.commit()
                return reusable
        await _video_providers(user_id)
        if not await resolve_provider_chain(db, user_id, "llm"):
            raise VideoPackStateError("未配置文本模型，无法撰写动作脚本")
        image_chain, image_error = await resolve_image_gen_chain(db, user_id, "reference", image_edit=True)
        if not image_chain:
            raise VideoPackStateError(image_error or "请配置图像编辑供应商以生成动作姿态")
        try:
            require_matting_model()
        except VideoProcessError as exc:
            raise VideoPackStateError(str(exc)) from exc
        reference_uri = await _process_thread(load_avatar_bytes_as_data_uri, outfit.fullbody_url)
        if not reference_uri:
            raise VideoPackStateError("外观参考图不可读")
        if source is not None:
            reference_path = source.reference_path
        else:
            reference_data = await _process_thread(base64.b64decode, reference_uri.split(",", 1)[1])
            reference_path = await save_companion_asset_async(
                reference_data,
                user_id=user_id,
                label="video_reference",
                ext=sniff_media_ext(reference_data) or "png",
            )
        active_outfit_id = (
            await db.execute(
                select(CompanionOutfit.id).where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True)),
            )
        ).scalar_one_or_none()
        context = GenerationContext(
            persona_definition=load_persona_definition(persona),
            personality_tags=safe_json_loads(persona.personality_tags_json or "[]", default=[]),
            outfit_description=outfit.description or "",
            feedback=feedback,
            active_outfit_id=active_outfit_id,
        )
        pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
        pack.reference_path = reference_path
        pack.context_json = context.model_dump_json()
        previous = {}
        if source is not None:
            previous = {
                job.action: job
                for job in (
                    await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == source.id))
                ).scalars()
            }
        for key in REQUIRED_VIDEO_ACTIONS:
            old = previous.get(key) if key != action else None
            job = CompanionVideoJob(
                user_id=user_id,
                pack_id=pack.id,
                outfit_id=outfit.id,
                action=key,
                status="queued",
                stage="script",
                reference_hash=reference_hash,
            )
            if old is not None:
                # 失败任务也保留句柄与素材，不能因重做另一个动作再次付费提交。
                for field in (
                    "status",
                    "stage",
                    "provider",
                    "model",
                    "provider_task_id",
                    "input_hash",
                    "script_json",
                    "artifact_path",
                    "result_json",
                    "pose_path",
                    "result_path",
                    "error",
                ):
                    setattr(job, field, getattr(old, field))
            db.add(job)
        await db.commit()
        await db.refresh(pack)
    _kick_generate(pack.id)
    return pack


def _can_resume_job(job: CompanionVideoJob) -> bool:
    return job.status != "succeeded" and bool(
        job.provider_task_id or job.artifact_path or job.stage == "script" or (job.stage == "pose" and job.pose_path),
    )


def _can_publish_jobs(jobs: list[CompanionVideoJob]) -> bool:
    return {job.action for job in jobs} == set(REQUIRED_VIDEO_ACTIONS) and all(
        job.status == "succeeded" and job.result_json for job in jobs
    )


async def retry_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionVideoPack:
    """只恢复已知任务或本地素材；不自动重发结果未知的生成请求。"""
    async with get_avatar_job_lock(user_id):
        await _reject_concurrent_build(db, user_id)
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None or pack.status != "failed" or not pack.reference_path:
            raise VideoPackStateError("该视频包没有可恢复任务")
        outfit = await db.get(CompanionOutfit, pack.outfit_id)
        avatar = (
            await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
        ).scalar_one_or_none()
        if outfit is None or await _reference_hash(outfit, avatar) != pack.reference_hash:
            raise VideoPackStateError("该视频包对应的参考已变更，请生成完整新包")
        jobs = list(
            (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == pack_id))).scalars(),
        )
        recoverable = [job for job in jobs if _can_resume_job(job)]
        if not recoverable and not _can_publish_jobs(jobs):
            raise VideoPackStateError("提交结果未知，请核对供应商任务后选择重做动作")
        for job in recoverable:
            job.status, job.error = "queued", None
        pack.status, pack.error = "processing", None
        await db.commit()
    _kick_generate(pack_id)
    return pack


async def _insert_pack(
    db: AsyncSession,
    user_id: int,
    *,
    avatar: AvatarAsset | None,
    outfit: CompanionOutfit,
    reference_hash: str,
) -> CompanionVideoPack:
    pack = CompanionVideoPack(
        user_id=user_id,
        avatar_id=avatar.id if avatar is not None else None,
        outfit_id=outfit.id,
        status="processing",
        reference_hash=reference_hash,
    )
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
    db.add(pack)
    await db.flush()
    return pack


def _kick_build(
    pack_id: int,
    *,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int],
    action_ranges: dict[str, tuple[float, float]],
) -> None:
    task = asyncio.create_task(
        _build_pack(
            pack_id,
            clips=clips,
            canvas=canvas,
            action_ranges=action_ranges,
        ),
        name=f"companion.video.build.{pack_id}",
    )
    _BUILD_TASKS.add(task)
    task.add_done_callback(_BUILD_TASKS.discard)


async def _prepare_clip_spec(
    work: Path,
    action: str,
    src: Path,
    user_id: int,
    canvas_w: int,
    canvas_h: int,
    *,
    start: float | None = None,
    end: float | None = None,
) -> tuple[VideoClipSpec, str]:
    """处理单个动作片段并转存资产；返回 (clip 描述符, 封面存储路径)。

    封面与命中遮罩从最终交付片段读取，使用 libvpx 解码 VP9 alpha。"""
    dst = work / f"{action}.{TARGET_EXT}"
    result = await _process_thread(
        prepare_action_clip,
        src,
        dst,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        start_seconds=start,
        end_seconds=end,
    )
    hitmask = await _process_thread(
        build_hitmask,
        dst,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
    )
    cover = work / f"{action}_cover.webp"
    await _process_thread(
        extract_cover,
        dst,
        cover,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
    )
    stored = await save_companion_asset_async(
        await _process_thread(dst.read_bytes),
        user_id=user_id,
        label=f"video_{action}",
        ext="webm",
    )
    try:
        cover_stored = await save_companion_asset_async(
            await _process_thread(cover.read_bytes),
            user_id=user_id,
            label=f"video_{action}_cover",
            ext="webp",
        )
    except BaseException:
        unlink_companion_asset(stored)
        raise
    return (
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
            hitmask_fps=HITMASK_FPS,
        ),
        cover_stored,
    )


async def _emit_pack_event(user_id: int, event_type: str, payload: dict) -> None:
    """独立短会话写入 WS 事件并提交，进度与结果在后台任务各阶段可发出。"""
    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
        await db.commit()


async def _advance_job(job_id: int, *, stage: str, status: str = "processing", **fields: object) -> None:
    """推进生成任务行阶段；终态行不再被覆写。"""
    async with SESSION_LOCAL() as db:
        job = await db.get(CompanionVideoJob, job_id)
        if job is None or job.status in ("succeeded", "failed"):
            return
        job.status = status
        job.stage = stage
        for key, value in fields.items():
            setattr(job, key, value)
        await db.commit()


async def _mark_jobs(
    db: AsyncSession,
    pack_id: int,
    *,
    status: str,
    stage: str | None = None,
    error: str | None = None,
) -> None:
    """批量改写包下任务行状态；stage 缺省保留各行最后推进到的阶段。"""
    values: dict[str, object] = {"status": status, "error": error}
    if stage is not None:
        values["stage"] = stage
    await db.execute(
        update(CompanionVideoJob)
        .where(CompanionVideoJob.pack_id == pack_id, CompanionVideoJob.status.not_in(("succeeded", "failed")))
        .values(**values)
        .execution_options(synchronize_session=False),
    )


async def _fail_pack(pack_id: int, exc: Exception) -> None:
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None or pack.status != "processing":
            return
        message = str(exc)[:500] or exc.__class__.__name__
        pack.status = "failed"
        pack.error = message
        await _mark_jobs(db, pack.id, status="failed", error=message)
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.video.failed",
            payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": message[:300]},
        )
        await db.commit()


async def _publish_ready(
    pack_id: int,
    *,
    canvas: tuple[int, int],
    clip_specs: list[VideoClipSpec],
    cover_path: str | None,
    auto_activate: bool,
) -> bool:
    """manifest 校验、落库发布 ready；迟到构建（参考版本已变化）按失败落库。

    自动激活只在生成路径使用：用户显式请求过生成，就绪即翻转为唯一激活包；
    发布和自动激活在同一事务中提交。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None:
            return False
        user_id = pack.user_id
    async with get_avatar_job_lock(user_id):
        return await _publish_ready_locked(
            pack_id,
            canvas=canvas,
            clip_specs=clip_specs,
            cover_path=cover_path,
            auto_activate=auto_activate,
        )


async def _publish_ready_locked(
    pack_id: int,
    *,
    canvas: tuple[int, int],
    clip_specs: list[VideoClipSpec],
    cover_path: str | None,
    auto_activate: bool,
) -> bool:
    canvas_w, canvas_h = canvas
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None or pack.status != "processing":
            return False
        manifest = VideoPackManifest(
            schema_version=MANIFEST_SCHEMA,
            pack_id=pack_id,
            appearance_id=pack.outfit_id,
            pack_version=pack.pack_version,
            canvas=VideoPackCanvas(width=canvas_w, height=canvas_h, fps=24),
            clips=clip_specs,
            cover_path=cover_path,
        )
        validate_pack_manifest(manifest)

        outfit = await db.get(CompanionOutfit, pack.outfit_id) if pack.outfit_id is not None else None
        avatar = (
            await db.execute(
                select(AvatarAsset).where(AvatarAsset.user_id == pack.user_id, AvatarAsset.active.is_(True)),
            )
        ).scalar_one_or_none()
        if outfit is None or await _reference_hash(outfit, avatar) != pack.reference_hash:
            pack.status = "failed"
            pack.error = "外观参考已变更，本次生成结果已过期，请重新生成"
            await _mark_jobs(db, pack.id, status="failed", stage="publish", error=pack.error)
            emit_ws_event(
                db,
                user_id=pack.user_id,
                event_type="companion.video.failed",
                payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": pack.error},
            )
            await db.commit()
            return False

    manifest_bytes = manifest.model_dump_json(indent=1).encode("utf-8")
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None or pack.status != "processing":
            return False
        manifest_path = await save_companion_asset_async(
            manifest_bytes,
            user_id=pack.user_id,
            label=f"video_pack_manifest_{pack.id}",
            ext="json",
        )
        pack.manifest_json = manifest_bytes.decode("utf-8")
        pack.manifest_path = manifest_path
        pack.content_hash = hashlib.sha256(manifest_bytes).hexdigest()
        pack.status = "ready"
        pack.error = None
        await _mark_jobs(db, pack.id, status="succeeded", stage="publish")
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.video.ready",
            payload={"packId": pack.id, "outfitId": pack.outfit_id, "packVersion": pack.pack_version},
        )
        if auto_activate:
            context = GenerationContext.model_validate_json(pack.context_json)
            active_outfit_id = (
                await db.execute(
                    select(CompanionOutfit.id).where(
                        CompanionOutfit.user_id == pack.user_id,
                        CompanionOutfit.active.is_(True),
                    ),
                )
            ).scalar_one_or_none()
            if active_outfit_id in (context.active_outfit_id, pack.outfit_id):
                await _activate_locked(db, pack)
        await db.commit()
    return True


async def _build_pack(
    pack_id: int,
    *,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int],
    action_ranges: dict[str, tuple[float, float]],
) -> None:
    """后台构建（上传导入）：逐动作处理 → manifest 校验 → 落库发布。"""
    canvas_w, canvas_h = canvas
    cover_path: str | None = None
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionVideoPack, pack_id)
            if pack is None:
                return
            user_id = pack.user_id

        clip_specs: list[VideoClipSpec] = []
        with tempfile.TemporaryDirectory(prefix="video-pack-") as tmp:
            work = Path(tmp)
            for action, (data, content_type) in clips.items():
                src = work / f"{action}_src{_source_ext(content_type)}"
                await _process_thread(src.write_bytes, data)
                start, end = action_ranges.get(action, (None, None))
                spec, cover_stored = await _prepare_clip_spec(
                    work,
                    action,
                    src,
                    user_id,
                    canvas_w,
                    canvas_h,
                    start=start,
                    end=end,
                )
                if action == "idle":
                    cover_path = cover_stored
                clip_specs.append(spec)
                async with SESSION_LOCAL() as db:
                    await db.execute(
                        update(CompanionVideoJob)
                        .where(CompanionVideoJob.pack_id == pack_id, CompanionVideoJob.action == action)
                        .values(
                            status="succeeded",
                            stage="publish",
                            result_path=spec.path,
                            result_json=ActionResult(clip=spec, cover_path=cover_stored).model_dump_json(),
                        ),
                    )
                    await db.commit()

        await _publish_ready(
            pack_id,
            canvas=(canvas_w, canvas_h),
            clip_specs=clip_specs,
            cover_path=cover_path,
            auto_activate=False,
        )
    except (VideoProcessError, ManifestValidationError, VideoPackError, OSError) as exc:
        await _fail_pack(pack_id, exc)
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底：失败必须落库可见
        logger.exception("video pack build crashed")
        await _fail_pack(pack_id, exc)


async def _video_providers(user_id: int) -> list[tuple[ProviderConfig, VideoGenProvider]]:
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen")
    providers = [
        (cfg, resolve(ServiceType.video_gen, cfg.provider_name)(cfg)) for cfg in chain if cfg.provider_name == "grok"
    ]
    providers = [
        (cfg, provider)
        for cfg, provider in providers
        if provider.supports_first_frame
        and provider.supports_loop_frames
        and all(duration in (provider.durations or ()) for duration in ACTION_DURATIONS.values())
    ]
    if not providers:
        raise VideoPackStateError("请配置支持首尾帧的 Grok 1.5 视频供应商；短动作不会回退到 MiniMax")
    return providers


async def _generate_pack(pack_id: int) -> None:
    """成功动作独立落盘；重启及重试不重做已成功动作，不重复提交未知付费请求。"""
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionVideoPack, pack_id)
            if pack is None or pack.status != "processing":
                return
            jobs = (
                (
                    await db.execute(
                        select(CompanionVideoJob)
                        .where(CompanionVideoJob.pack_id == pack_id)
                        .order_by(CompanionVideoJob.id),
                    )
                )
                .scalars()
                .all()
            )
        context = GenerationContext.model_validate_json(pack.context_json)
        pending = [job for job in jobs if job.status != "succeeded" and job.status != "failed" and not job.script_json]
        if pending:
            await _emit_pack_event(pack.user_id, "companion.video.progress", {"packId": pack_id, "stage": "script"})
            script = await compose_action_script(
                None,
                pack.user_id,
                persona_definition=context.persona_definition,
                personality_tags=context.personality_tags,
                outfit_description=context.outfit_description,
                actions=tuple(job.action for job in pending),
                feedback=context.feedback,
            )
            async with SESSION_LOCAL() as db:
                for job, entry in zip(pending, script.actions, strict=True):
                    job.script_json = entry.model_dump_json()
                    await db.execute(
                        update(CompanionVideoJob)
                        .where(CompanionVideoJob.id == job.id)
                        .values(script_json=job.script_json),
                    )
                await db.commit()
        for job in jobs:
            if job.status in ("succeeded", "failed"):
                continue
            try:
                await _generate_action(pack, job)
            except Exception as exc:  # noqa: BLE001 — 动作失败隔离，其他已请求动作仍可交付并独立重做
                logger.exception("video action failed", extra={"pack_id": pack_id, "action": job.action})
                message = _generation_error(exc)
                await _advance_job(job.id, stage=job.stage, status="failed", error=message)
        async with SESSION_LOCAL() as db:
            results = (
                (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == pack_id)))
                .scalars()
                .all()
            )
        failed = [job for job in results if not job.result_json or job.status != "succeeded"]
        if failed:
            await _fail_pack(
                pack_id,
                VideoPackError("；".join(f"{job.action}: {job.error or '动作未完成'}" for job in failed)),
            )
            return
        specs: list[VideoClipSpec] = []
        cover_path = None
        for job in sorted(results, key=lambda job: REQUIRED_VIDEO_ACTIONS.index(job.action)):
            result = ActionResult.model_validate_json(job.result_json or "")
            specs.append(result.clip)
            if job.action == "idle":
                cover_path = result.cover_path
        await _emit_pack_event(pack.user_id, "companion.video.progress", {"packId": pack_id, "stage": "publish"})
        await _publish_ready(
            pack_id,
            canvas=_DEFAULT_CANVAS,
            clip_specs=specs,
            cover_path=cover_path,
            auto_activate=True,
        )
    except Exception as exc:  # noqa: BLE001 — 后台异常必须转为持久可见失败
        logger.exception("video pack generation failed", extra={"pack_id": pack_id})
        await _fail_pack(pack_id, VideoPackError(_generation_error(exc)))


def _generation_error(exc: Exception) -> str:
    if isinstance(exc, ProviderResultUnknownError):
        return "提交结果未知，未自动重发；请核对供应商任务后再决定是否重做"
    if isinstance(exc, ProviderError):
        return _provider_failure_copy(str(exc))
    if isinstance(exc, (VideoProcessError, VideoPackError, VideoScriptError, ImageGenerationError)):
        return str(exc)[:500]
    return "动作处理失败，可重试已有任务或素材"


async def _generate_action(pack: CompanionVideoPack, job: CompanionVideoJob) -> None:
    async def progress(stage: str) -> None:
        job.stage = stage
        await _advance_job(job.id, stage=stage)
        await _emit_pack_event(
            pack.user_id,
            "companion.video.progress",
            {"packId": pack.id, "stage": stage, "action": job.action},
        )

    entry = ActionScriptEntry.model_validate_json(job.script_json or "{}")
    if not job.artifact_path:
        if not job.provider_task_id:
            providers = await _video_providers(pack.user_id)
            if job.stage == "submit":
                raise VideoPackError("提交结果未知，未自动重发；请核对供应商任务")
            reference_uri = await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))
            if not job.pose_path:
                if job.stage == "pose":
                    raise VideoPackError("动作姿态生成结果未知，请核对后重做此动作")
                await progress("pose")
                paths = await generate_images(
                    build_pose_prompt(entry),
                    user_id=pack.user_id,
                    reference_image=reference_uri,
                    size="1024x1536",
                    image_edit=True,
                    persist_user_assets=True,
                )
                job.pose_path = paths[0]
                await _advance_job(job.id, stage="pose", pose_path=job.pose_path)
            pose_uri = await _process_thread(_image_data_uri, _artifact_abs_path(job.pose_path))
            submitted: dict[str, str] = {}

            async def submit(provider: VideoGenProvider) -> VideoJobStatus:
                submitted.update(provider=provider.provider_name, model=provider.config.model)
                return await provider.submit(
                    VideoGenRequest(
                        prompt=build_video_prompt(entry),
                        duration=ACTION_DURATIONS[entry.action],
                        resolution="720p",
                        first_frame_image=pose_uri,
                        last_frame_image=pose_uri,
                        reference_images=(reference_uri,),
                    ),
                )

            await progress("submit")
            status = await execute_with_fallback(
                None,
                pack.user_id,
                "video_gen",
                call_fn=submit,
                _chain=[cfg for cfg, _ in providers],
            )
            if not status.task_id:
                raise ProviderResultUnknownError("视频提交未返回任务句柄")
            job.provider, job.model, job.provider_task_id = submitted["provider"], submitted["model"], status.task_id
            await _advance_job(
                job.id,
                stage="generate",
                provider=job.provider,
                model=job.model,
                provider_task_id=job.provider_task_id,
            )
        await progress("generate")
        provider = await _pinned_provider(pack.user_id, job.provider, job.model or "")
        status = await _poll_generation(provider, job.provider_task_id)
        if status.status != "succeeded":
            raise VideoPackError(_provider_failure_copy(status.error))
        await progress("download")
        url = status.download_url
        if not url and status.file_id:
            url = (await provider.fetch(status.file_id)).download_url
        if not url:
            raise VideoPackError("供应商未返回视频下载地址")
        data = await download_capped(url, max_bytes=MAX_SOURCE_BYTES, timeout=180)
        ext = sniff_media_ext(data)
        if ext not in _SOURCE_MEDIA_EXTS:
            raise VideoPackError("供应商返回了不支持的视频格式")
        job.artifact_path = await save_companion_asset_async(
            data,
            user_id=pack.user_id,
            label=f"video_{job.action}_source",
            ext=ext,
        )
        await _advance_job(job.id, stage="process", artifact_path=job.artifact_path)
    await progress("process")
    with tempfile.TemporaryDirectory(prefix="video-action-") as tmp:
        work = Path(tmp)
        matte = work / "matte.mkv"
        await _process_thread(matte_video, _artifact_abs_path(job.artifact_path), matte)
        loop = await _process_thread(select_loop, matte, max_seconds=ACTION_DURATIONS[entry.action])
        spec, cover = await _prepare_clip_spec(
            work,
            job.action,
            matte,
            pack.user_id,
            *_DEFAULT_CANVAS,
            start=loop.start,
            end=loop.end,
        )
        await _advance_job(
            job.id,
            stage="publish",
            status="succeeded",
            result_path=spec.path,
            result_json=ActionResult(clip=spec, cover_path=cover, quality=loop).model_dump_json(),
            error=None,
        )


async def _pinned_provider(
    user_id: int,
    provider_name: str,
    model_name: str,
) -> VideoGenProvider:
    """轮询用供应商实例钉死在提交时的 provider+model 上（任务 ID 跨协议不通用）。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen")
    matched = next((cfg for cfg in chain if cfg.provider_name == provider_name), None)
    if matched is None:
        raise VideoPackError("视频供应商配置已变更，请重新生成", internal=f"provider {provider_name} missing")
    if model_name and model_name != matched.model:
        matched = replace(matched, model=model_name)
    return resolve(ServiceType.video_gen, matched.provider_name)(matched)


_POLICY_KEYWORDS = ("policy", "unsafe", "content_filter", "敏感", "违规", "moderation")


def _provider_failure_copy(error: str | None) -> str:
    """供应商失败的用户文案：策略审核仅关键词嗅探，原始错误文本不外泄。"""
    if error and any(keyword in error.lower() for keyword in _POLICY_KEYWORDS):
        return "内容审核未通过，请调整角色形象后重试"
    return "视频生成失败，请稍后重试"


async def _poll_generation(provider: VideoGenProvider, task_id: str) -> VideoJobStatus:
    """有界轮询：超时上限与退避沿用聊天视频生成配置。"""
    deadline = utc_now() + timedelta(seconds=SETTINGS.video_gen_max_poll_seconds)
    attempt = 0
    last_status: str | None = None
    while True:
        remaining = max(0.0, (deadline - utc_now()).total_seconds())
        if remaining <= 0:
            raise VideoPackError("视频生成超时，请稍后重试")
        status = await provider.poll(task_id)
        if status.status in ("succeeded", "failed"):
            return status
        if last_status is not None and status.status != last_status:
            attempt = 0
        elif last_status is not None:
            attempt += 1
        last_status = status.status
        sleep_for = backoff_for_poll(
            attempt,
            base_interval=SETTINGS.video_gen_poll_interval_seconds,
            max_interval=SETTINGS.video_gen_poll_backoff_max_seconds,
            remaining_seconds=remaining,
        )
        if sleep_for <= 0:
            raise VideoPackError("视频生成超时，请稍后重试")
        await asyncio.sleep(sleep_for)


def _kick_generate(pack_id: int) -> None:
    if pack_id in _GEN_INFLIGHT:
        return
    task = asyncio.create_task(_generate_pack(pack_id), name=f"companion.video.generate.{pack_id}")
    _GEN_TASKS.add(task)
    _GEN_INFLIGHT.add(pack_id)

    def _done(_task: asyncio.Task[None]) -> None:
        _GEN_TASKS.discard(_task)
        _GEN_INFLIGHT.discard(pack_id)

    task.add_done_callback(_done)


async def drain_video_generation() -> None:
    """停机时取消并等待生成任务；已提交的供应商任务由重启恢复凭句柄续轮询。"""
    tasks = list(_GEN_TASKS | _BUILD_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def resume_video_generation_jobs() -> None:
    async with SESSION_LOCAL() as db:
        packs = (
            (
                await db.execute(
                    select(CompanionVideoPack).where(
                        CompanionVideoPack.status == "processing",
                        CompanionVideoPack.reference_path != "",
                    ),
                )
            )
            .scalars()
            .all()
        )
    for pack in packs:
        _kick_generate(pack.id)


async def _activate_locked(db: AsyncSession, pack: CompanionVideoPack) -> None:
    await db.execute(
        update(CompanionVideoPack)
        .where(CompanionVideoPack.user_id == pack.user_id, CompanionVideoPack.active.is_(True))
        .values(active=False)
        .execution_options(synchronize_session=False),
    )
    # bulk update synchronize_session=False 后必须显式标脏，重启激活同一行也能写回。
    pack.active = True
    flag_modified(pack, "active")
    await db.execute(
        update(CompanionOutfit)
        .where(CompanionOutfit.user_id == pack.user_id)
        .values(active=False)
        .execution_options(synchronize_session=False),
    )
    await db.execute(
        update(CompanionOutfit)
        .where(CompanionOutfit.id == pack.outfit_id, CompanionOutfit.user_id == pack.user_id)
        .values(active=True)
        .execution_options(synchronize_session=False),
    )
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.outfit.updated",
        payload={"outfit_id": pack.outfit_id, "worn": True},
    )
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.video.activated",
        payload={"packId": pack.id, "packVersion": pack.pack_version, "outfitId": pack.outfit_id},
    )


async def activate_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionVideoPack:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError("找不到视频包")
        if pack.status != "ready":
            raise VideoPackStateError("视频包尚未就绪")
        outfit = await db.get(CompanionOutfit, pack.outfit_id)
        avatar = (
            await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
        ).scalar_one_or_none()
        if outfit is None or await _reference_hash(outfit, avatar) != pack.reference_hash:
            raise VideoPackStateError("该视频包对应的参考已变更")
        await _activate_locked(db, pack)
        await db.commit()
        await db.refresh(pack)
    return pack


def _pack_assets(pack: CompanionVideoPack, jobs: list[CompanionVideoJob]) -> set[str]:
    paths = {pack.reference_path, pack.manifest_path}
    if pack.manifest_json:
        manifest = VideoPackManifest.model_validate_json(pack.manifest_json)
        paths.add(manifest.cover_path or "")
        paths.update(clip.path for clip in manifest.clips)
    for job in jobs:
        paths.update((job.artifact_path or "", job.result_path or "", job.pose_path or ""))
        if job.result_json:
            result = ActionResult.model_validate_json(job.result_json)
            paths.update((result.clip.path, result.cover_path))
    return paths - {""}


async def delete_pack(db: AsyncSession, user_id: int, pack_id: int) -> None:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError("找不到视频包")
        if pack.active or pack.status == "processing":
            raise VideoPackStateError("使用中或构建中的视频包不能删除")
        packs = (
            (await db.execute(select(CompanionVideoPack).where(CompanionVideoPack.user_id == user_id))).scalars().all()
        )
        jobs = (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.user_id == user_id))).scalars().all()
        owned = [job for job in jobs if job.pack_id == pack_id]
        candidates = _pack_assets(pack, owned)
        for other in packs:
            if other.id != pack_id:
                candidates -= _pack_assets(other, [job for job in jobs if job.pack_id == other.id])
        for job in owned:
            await db.delete(job)
        await db.delete(pack)
        await db.commit()
        # 单动作版本会共享资源，只有最后一个引用消失才回收文件。
        for path in candidates:
            with contextlib.suppress(OSError):
                unlink_companion_asset(path)


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
    jobs = (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.user_id == user_id))).scalars().all()
    jobs_by_pack: dict[int, list[CompanionVideoJob]] = {}
    for job in jobs:
        if job.pack_id is not None:
            jobs_by_pack.setdefault(job.pack_id, []).append(job)
    responses = []
    for pack in packs:
        response = pack_response(pack)
        pack_jobs = jobs_by_pack.get(pack.id, [])
        response["can_regenerate"] = bool(pack.reference_path) and pack.status in ("ready", "failed")
        response["can_retry"] = (
            bool(pack.reference_path)
            and pack.status == "failed"
            and (any(_can_resume_job(job) for job in pack_jobs) or _can_publish_jobs(pack_jobs))
        )
        response["actions"] = []
        for job in sorted(pack_jobs, key=lambda job: REQUIRED_VIDEO_ACTIONS.index(job.action)):
            script = ActionScriptEntry.model_validate_json(job.script_json) if job.script_json else None
            response["actions"].append(
                {
                    "action": job.action,
                    "status": job.status,
                    "stage": job.stage,
                    "error": job.error,
                    "clip_url": signed_companion_asset_url(job.result_path) if job.result_path else None,
                    "motion_prompt": script.motion_prompt if script else "",
                },
            )
        responses.append(response)
    return responses


async def resume_processing_packs() -> None:
    """进程重启恢复：无可续跑句柄的 processing 包按失败落库并广播；上传导入的源片段
    在临时工作区、重启后不可复用，用户显式重交即可。有句柄的生成任务由
    resume_video_generation_jobs 续跑，此处跳过。"""
    async with SESSION_LOCAL() as db:
        packs = (
            (await db.execute(select(CompanionVideoPack).where(CompanionVideoPack.status == "processing")))
            .scalars()
            .all()
        )
        if not packs:
            return
        resumable_ids = {pack.id for pack in packs if pack.reference_path}
        failed = 0
        for pack in packs:
            if pack.id in resumable_ids:
                continue
            pack.status = "failed"
            pack.error = "处理进程重启，请重新提交"
            await _mark_jobs(db, pack.id, status="failed", error="process restarted")
            emit_ws_event(
                db,
                user_id=pack.user_id,
                event_type="companion.video.failed",
                payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": "process restarted"},
            )
            failed += 1
        if failed:
            await db.commit()
            logger.info("resumed video packs marked failed", extra={"count": failed})
