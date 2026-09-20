"""视频动作包编排：外观版本 → 动作片段处理 → 不可变包发布 → 激活。

状态与阶段分离（status × stage）逐任务持久化；FFmpeg 等待走工作线程，
不占数据库长事务。发布与激活共用头像用户级锁：发布凭 reference_hash 拒绝迟到
结果，新包构建失败不清空旧激活包。

两条创建入口：
- 上传导入：`create_pack_from_clips`，用户提供已制作片段（或长视频区间）；
- 按参考生成：`create_pack_from_reference`，LLM 演绎脚本 → 参考图 i2v 生成 →
  服务端抠像与动作分割 → 与上传路径相同的切分、编码与发布链，成功后自动激活。
  供应商任务句柄在提交后立即落库，重启后凭句柄续轮询，不重复提交付费任务。
"""

import asyncio
import contextlib
import hashlib
import tempfile
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

from services.domains.companion import get_or_create_persona, load_persona_definition
from services.infrastructure.assets import (
    save_companion_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
    unlink_companion_asset,
)
from services.infrastructure.llm import (
    MissingLlmConfigError,
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
from services.infrastructure.video_processing.matting import matte_video
from services.infrastructure.video_processing.process import HITMASK_GRID_H, HITMASK_GRID_W
from services.infrastructure.video_processing.segmentation import segment_actions

from ..avatar_service import get_avatar_job_lock, load_avatar_bytes_as_data_uri
from .manifest import (
    MANIFEST_SCHEMA,
    ManifestValidationError,
    VideoClipSpec,
    VideoPackCanvas,
    VideoPackManifest,
    validate_pack_manifest,
)
from .script import ActionScript, VideoScriptError, build_video_prompt, compose_action_script

logger = get_logger(__name__)

_DEFAULT_CANVAS = (512, 768)
_SOURCE_EXT_BY_MIME = {
    "video/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}
# 按参考生成任务在 companion_video_jobs 的哨兵动作键：一条任务生成整包长视频，
# 切分后的逐动作处理沿用上传路径的逐动作语义（stage 覆盖 script→publish 全链）。
FULL_PACK_ACTION = "full"
# 生成源接受的视频容器（供应商产物与内部中间产物）
_SOURCE_MEDIA_EXTS = {"mp4", "webm", "mov", "mkv"}

_BUILD_TASKS: set[asyncio.Task[None]] = set()
_GEN_TASKS: set[asyncio.Task[None]] = set()
_GEN_INFLIGHT: set[int] = set()


class VideoPackError(RuntimeError):
    """视频包流程错误；str 为公开文案，internal 保留定位线索。"""

    def __init__(self, message: str, *, internal: str | None = None) -> None:
        super().__init__(message)
        self.internal = internal


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
    if not clips:
        raise VideoPackError("请至少提供一个动作片段")
    missing = [action for action in REQUIRED_VIDEO_ACTIONS if action not in clips]
    if missing:
        raise VideoPackError("缺少必需动作片段：" + "、".join(missing))

    canvas_w, canvas_h = canvas
    if not (0 < canvas_w <= MAX_CANVAS_WIDTH and 0 < canvas_h <= MAX_CANVAS_HEIGHT):
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
        reference_hash = _reference_hash(outfit, avatar)

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
) -> CompanionVideoPack:
    """按参考生成视频包：LLM 演绎脚本 → 参考图 i2v → 服务端抠像与分割 → 发布后自动激活。

    outfit_id 缺省取当前激活且已确认的外观；force=False 时同参考版本的激活就绪包直接复用，
    不重复触发付费生成。提交与轮询在后台任务内进行，结果经 companion.video 事件回流。"""
    async with get_avatar_job_lock(user_id):
        persona = await get_or_create_persona(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成 onboarding 再生成视频形象")
        await _reject_concurrent_build(db, user_id)

        if outfit_id is not None:
            outfit = (
                await db.execute(
                    select(CompanionOutfit).where(CompanionOutfit.id == outfit_id, CompanionOutfit.user_id == user_id),
                )
            ).scalar_one_or_none()
            if outfit is None:
                raise VideoPackError("找不到对应的外观")
        else:
            outfit = (
                await db.execute(
                    select(CompanionOutfit)
                    .where(
                        CompanionOutfit.user_id == user_id,
                        CompanionOutfit.status == "ready",
                        CompanionOutfit.active.is_(True),
                    )
                    .order_by(CompanionOutfit.id.desc())
                    .limit(1),
                )
            ).scalar_one_or_none()
            if outfit is None:
                raise VideoPackStateError("请先在外观页确认外观参考立绘")
        if outfit.status != "ready":
            raise VideoPackStateError("外观尚未确认，无法生成视频形象")
        avatar = (
            await db.execute(
                select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)),
            )
        ).scalar_one_or_none()
        reference_hash = _reference_hash(outfit, avatar)

        if not force:
            reusable = (
                await db.execute(
                    select(CompanionVideoPack)
                    .where(
                        CompanionVideoPack.user_id == user_id,
                        CompanionVideoPack.outfit_id == outfit.id,
                        CompanionVideoPack.reference_hash == reference_hash,
                        CompanionVideoPack.status == "ready",
                        CompanionVideoPack.active.is_(True),
                    )
                    .limit(1),
                )
            ).scalar_one_or_none()
            if reusable is not None:
                return reusable

        # 复用检查之后才读参考图字节：可复用的已有包不依赖源图当前可读。
        reference_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, outfit.fullbody_url)
        if not reference_uri:
            raise VideoPackStateError("外观参考图缺失或无法读取，请重新确认外观")

        # 供应商可用性前置校验：避免建包后立即失败。脚本用 llm 链，视频用 video_gen 链。
        llm_chain = await resolve_provider_chain(db, user_id, "llm")
        if not llm_chain:
            raise VideoPackStateError("未配置文本模型供应商，无法撰写动作脚本")
        video_chain = await resolve_provider_chain(db, user_id, "video_gen")
        if not video_chain:
            raise VideoPackStateError("未配置视频生成供应商，请先在管理页配置")
        if not any(
            resolve(ServiceType.video_gen, config.provider_name)(config).supports_first_frame for config in video_chain
        ):
            raise VideoPackStateError("当前视频供应商不支持以参考图生成视频，请更换供应商后重试")

        pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
        db.add(
            CompanionVideoJob(
                user_id=user_id,
                pack_id=pack.id,
                outfit_id=outfit.id,
                action=FULL_PACK_ACTION,
                status="queued",
                stage="script",
                reference_hash=reference_hash,
            ),
        )
        await db.commit()
        await db.refresh(pack)

    _kick_generate(pack.id)
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
) -> tuple[VideoClipSpec, str | None]:
    """处理单个动作片段并转存资产；返回 (clip 描述符, 封面存储路径)。

    src 须为像素级 alpha 的中间产物（FFV1）：命中遮罩与封面依赖可解码的 alpha，
    交付用 WebM/VP9 的 alpha 走封装旁路，FFmpeg 解码不可见。"""
    dst = work / f"{action}.{TARGET_EXT}"
    result = await asyncio.to_thread(
        prepare_action_clip,
        src,
        dst,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        start_seconds=start,
        end_seconds=end,
    )
    stored = await save_companion_asset_async(
        await asyncio.to_thread(dst.read_bytes),
        user_id=user_id,
        label=f"video_{action}",
        ext="webm",
    )
    hitmask = await asyncio.to_thread(
        build_hitmask,
        src,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        start_seconds=start or 0.0,
        end_seconds=end,
    )
    cover = work / f"{action}_cover.webp"
    await asyncio.to_thread(
        extract_cover,
        src,
        cover,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        at_seconds=start or 0.0,
    )
    cover_stored = await save_companion_asset_async(
        await asyncio.to_thread(cover.read_bytes),
        user_id=user_id,
        label=f"video_{action}_cover",
        ext="webp",
    )
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
        .where(CompanionVideoJob.pack_id == pack_id)
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
        if outfit is None or _reference_hash(outfit, avatar) != pack.reference_hash:
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
            await db.execute(
                update(CompanionVideoPack)
                .where(CompanionVideoPack.user_id == pack.user_id, CompanionVideoPack.active.is_(True))
                .values(active=False)
                .execution_options(synchronize_session=False),
            )
            pack.active = True
            emit_ws_event(
                db,
                user_id=pack.user_id,
                event_type="companion.video.activated",
                payload={"packId": pack.id, "packVersion": pack.pack_version, "outfitId": pack.outfit_id},
            )
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
                src.write_bytes(data)
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


class _GenerationState:
    """从持久化行恢复的生成任务状态：任一句柄存在即从对应阶段续跑。"""

    def __init__(
        self,
        *,
        user_id: int,
        pack_id: int,
        outfit_id: int,
        job_id: int,
        canvas: tuple[int, int],
        script: ActionScript | None,
        provider_name: str,
        model_name: str,
        provider_task_id: str,
        artifact_path: str,
    ) -> None:
        self.user_id = user_id
        self.pack_id = pack_id
        self.outfit_id = outfit_id
        self.job_id = job_id
        self.canvas = canvas
        self.script = script
        self.provider_name = provider_name
        self.model_name = model_name
        self.provider_task_id = provider_task_id
        self.artifact_path = artifact_path


async def _load_generation_state(db: AsyncSession, pack_id: int) -> _GenerationState | None:
    pack = await db.get(CompanionVideoPack, pack_id)
    if pack is None or pack.status != "processing":
        return None
    job = (
        await db.execute(
            select(CompanionVideoJob).where(
                CompanionVideoJob.pack_id == pack_id,
                CompanionVideoJob.action == FULL_PACK_ACTION,
            ),
        )
    ).scalar_one_or_none()
    if job is None or job.outfit_id is None:
        return None
    script: ActionScript | None = None
    if job.script_json:
        try:
            script = ActionScript.model_validate_json(job.script_json)
        except ValueError:
            logger.warning("stored action script unparsable", extra={"pack_id": pack_id})
    return _GenerationState(
        user_id=pack.user_id,
        pack_id=pack.id,
        outfit_id=job.outfit_id,
        job_id=job.id,
        canvas=_DEFAULT_CANVAS,
        script=script,
        provider_name=job.provider or "",
        model_name=job.model or "",
        provider_task_id=job.provider_task_id or "",
        artifact_path=job.artifact_path or "",
    )


async def _video_providers(user_id: int) -> list[tuple[ProviderConfig, VideoGenProvider]]:
    """解析 video_gen 供应商链并实例化；链空抛出可展示错误。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen")
    if not chain:
        raise VideoPackStateError("未配置视频生成供应商，请先在管理页配置")
    providers: list[tuple[ProviderConfig, VideoGenProvider]] = []
    for config in chain:
        providers.append((config, resolve(ServiceType.video_gen, config.provider_name)(config)))
    return providers


def _chain_duration_budget(providers: list[tuple[ProviderConfig, VideoGenProvider]]) -> int:
    """时长预算取链上各供应商最大时长档的最小值：回退到任何一家都装得下脚本节奏。"""
    declared = [max(p.durations) for _, p in providers if p.durations]
    return min(declared) if declared else 6


async def _generate_pack(pack_id: int) -> None:
    """按参考生成全链：脚本 → 提交 → 轮询 → 下载 → 抠像与分割 → 发布 → 自动激活。

    凭持久化句柄断点续跑：有 script_json 跳过脚本，有 provider_task_id 跳过提交，
    有 artifact_path 直接进入处理；每阶段推进落库并发进度事件。
    """
    async with SESSION_LOCAL() as db:
        state = await _load_generation_state(db, pack_id)
    if state is None:
        return
    user_id = state.user_id
    try:
        if state.artifact_path:
            await _emit_pack_event(user_id, "companion.video.progress", {"packId": pack_id, "stage": "process"})
            await _process_generation(state)
            return

        providers = await _video_providers(user_id)
        first_frame_providers = [(cfg, p) for cfg, p in providers if p.supports_first_frame]
        if not first_frame_providers:
            raise VideoPackStateError("当前视频供应商不支持以参考图生成视频，请更换供应商后重试")

        async with SESSION_LOCAL() as db:
            persona = await get_or_create_persona(db, user_id)
            outfit = await db.get(CompanionOutfit, state.outfit_id)
        if outfit is None or outfit.status != "ready":
            raise VideoPackStateError("外观参考已不可用，请重新确认外观")

        # 阶段 1：演绎脚本
        if state.script is None:
            await _advance_job(state.job_id, stage="script")
            await _emit_pack_event(
                user_id,
                "companion.video.progress",
                {"packId": pack_id, "stage": "script"},
            )
            personality_tags = safe_json_loads(persona.personality_tags_json or "[]", default=[])
            if not isinstance(personality_tags, list):
                personality_tags = []
            script = await compose_action_script(
                None,
                user_id,
                persona_definition=load_persona_definition(persona),
                personality_tags=[str(tag) for tag in personality_tags],
                outfit_description=outfit.description or "",
                duration_budget=_chain_duration_budget(first_frame_providers),
            )
            await _advance_job(state.job_id, stage="script", script_json=script.model_dump_json())
            state.script = script
        script = state.script

        # 阶段 2：提交供应商（首帧 = 外观参考立绘）
        if not state.provider_task_id:
            await _emit_pack_event(
                user_id,
                "companion.video.progress",
                {"packId": pack_id, "stage": "submit"},
            )
            reference_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, outfit.fullbody_url)
            if not reference_uri:
                raise VideoPackStateError("外观参考图缺失或无法读取，请重新确认外观")

            submitted: dict[str, str] = {}

            async def _submit(provider: VideoGenProvider) -> VideoJobStatus:
                duration = max(provider.durations) if provider.durations else 6
                resolution = provider.resolutions[0] if provider.resolutions else "768P"
                submitted["provider"] = provider.provider_name
                submitted["model"] = provider.config.model
                return await provider.submit(
                    VideoGenRequest(
                        prompt=build_video_prompt(script),
                        duration=duration,
                        resolution=resolution,
                        first_frame_image=reference_uri,
                    ),
                )

            # 提交开始前持久化边界；无任务句柄的 submit 状态不能自动重发。
            await _advance_job(state.job_id, stage="submit")
            chain = [cfg for cfg, _p in first_frame_providers]
            status = await execute_with_fallback(None, user_id, "video_gen", call_fn=_submit, _chain=chain)
            # 提交成功先落任务句柄再轮询，避免轮询期崩溃丢失已付费任务。
            await _advance_job(
                state.job_id,
                stage="generate",
                provider=submitted.get("provider", ""),
                model=submitted.get("model", ""),
                provider_task_id=status.task_id,
            )
            state.provider_name = submitted.get("provider", "")
            state.model_name = submitted.get("model", "")
            state.provider_task_id = status.task_id

        # 阶段 3：轮询
        await _emit_pack_event(
            user_id,
            "companion.video.progress",
            {"packId": pack_id, "stage": "generate"},
        )
        provider = _pinned_provider(first_frame_providers, state.provider_name, state.model_name)
        status = await _poll_generation(provider, state.provider_task_id)
        if status.status != "succeeded":
            raise VideoPackError(_provider_failure_copy(status.error))

        # 阶段 4：下载
        await _advance_job(state.job_id, stage="download")
        await _emit_pack_event(
            user_id,
            "companion.video.progress",
            {"packId": pack_id, "stage": "download"},
        )
        if not state.artifact_path:
            url = status.download_url
            if not url:
                if not status.file_id:
                    raise VideoPackError("视频生成失败，请稍后重试")
                url = (await provider.fetch(status.file_id)).download_url
            data = await download_capped(url, max_bytes=SETTINGS.video_gen_download_max_bytes, timeout=600.0)
            ext = sniff_media_ext(data)
            if ext not in _SOURCE_MEDIA_EXTS:
                logger.warning("provider returned unsupported media", extra={"pack_id": pack_id, "sniffed": ext})
                raise VideoPackError("供应商返回了不支持的视频格式")
            stored = await save_companion_asset_async(
                data,
                user_id=user_id,
                label=f"video_pack_source_{pack_id}",
                ext=ext,
            )
            await _advance_job(state.job_id, stage="process", artifact_path=stored)
            state.artifact_path = stored

        # 阶段 5：抠像、分割、切分与发布
        await _emit_pack_event(
            user_id,
            "companion.video.progress",
            {"packId": pack_id, "stage": "process"},
        )
        await _process_generation(state)
    except ProviderResultUnknownError:
        await _fail_pack(
            pack_id,
            VideoPackError("视频提交结果不确定，供应商可能已接单；为避免重复计费未自动重试，请稍后重试"),
        )
    except ProviderError as exc:
        # 供应商原始错误文本只进日志；用户可见文案走脱敏映射（提交拒绝 / 轮询失败同一路径）。
        logger.warning("video provider rejected generation", extra={"pack_id": pack_id, "error": str(exc)})
        await _fail_pack(pack_id, VideoPackError(_provider_failure_copy(str(exc))))
    except (
        VideoProcessError,
        ManifestValidationError,
        VideoPackError,
        VideoScriptError,
        OSError,
        MissingLlmConfigError,
    ) as exc:
        await _fail_pack(pack_id, exc)
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底：失败必须落库可见
        logger.exception("video pack generation crashed")
        await _fail_pack(pack_id, exc)


def _pinned_provider(
    providers: list[tuple[ProviderConfig, VideoGenProvider]],
    provider_name: str,
    model_name: str,
) -> VideoGenProvider:
    """轮询用供应商实例钉死在提交时的 provider+model 上（任务 ID 跨协议不通用）。"""
    matched = next((cfg for cfg, p in providers if cfg.provider_name == provider_name), None)
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


async def _process_generation(state: _GenerationState) -> None:
    """生成源 → 抠像 → 动作分割 → 逐段切分编码 → 发布并自动激活。"""
    if state.script is None:
        raise VideoPackError("演绎脚本缺失，请重新生成")
    source = _artifact_abs_path(state.artifact_path)
    if not source.exists():
        raise VideoPackError("生成源视频已丢失，请重新生成", internal=f"missing {source}")

    canvas_w, canvas_h = state.canvas
    cover_path: str | None = None
    with tempfile.TemporaryDirectory(prefix="video-gen-") as tmp:
        work = Path(tmp)
        matte = work / "matte.mkv"
        matte_result = await asyncio.to_thread(matte_video, source, matte)
        segmentation = await asyncio.to_thread(segment_actions, matte, len(state.script.actions))
        logger.info(
            "video pack generation processed",
            extra={
                "pack_id": state.pack_id,
                "matte": matte_result.method,
                "segmentation": segmentation.method,
                "segments": [(round(s, 2), round(e, 2)) for s, e in segmentation.segments],
            },
        )

        clip_specs: list[VideoClipSpec] = []
        for entry, (start, end) in zip(state.script.actions, segmentation.segments, strict=True):
            spec, cover_stored = await _prepare_clip_spec(
                work,
                entry.action,
                matte,
                state.user_id,
                canvas_w,
                canvas_h,
                start=start,
                end=end,
            )
            if entry.action == "idle":
                cover_path = cover_stored
            clip_specs.append(spec)

    await _publish_ready(
        state.pack_id,
        canvas=(canvas_w, canvas_h),
        clip_specs=clip_specs,
        cover_path=cover_path,
        auto_activate=True,
    )
    # 源视频保留不随发布清理（job.artifact_path 继续指向它）：切分与抠像仍会迭代，
    # 保留源便于离线复盘与重切；随包删除时在 delete_pack 一并清理。


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
    tasks = list(_GEN_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def resume_video_generation_jobs() -> None:
    """进程重启恢复：按持久化句柄续跑（任务 ID 续轮询 / 产物续处理 / 脚本续提交），
    提交中但无供应商句柄的任务按结果未知失败落库，不自动重发。"""
    async with SESSION_LOCAL() as db:
        jobs = (
            (
                await db.execute(
                    select(CompanionVideoJob).where(
                        CompanionVideoJob.action == FULL_PACK_ACTION,
                        CompanionVideoJob.status.in_(("queued", "processing")),
                        CompanionVideoJob.pack_id.is_not(None),
                    ),
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            if job.provider_task_id or job.artifact_path or (job.script_json and job.stage == "script"):
                _kick_generate(job.pack_id)
                continue
            job.status = "failed"
            job.error = (
                "视频提交结果不确定，供应商可能已接单；为避免重复计费未自动重试"
                if job.stage == "submit"
                else "处理进程重启，请重新发起生成"
            )
            pack = await db.get(CompanionVideoPack, job.pack_id)
            if pack is not None and pack.status == "processing":
                pack.status = "failed"
                pack.error = job.error
                emit_ws_event(
                    db,
                    user_id=pack.user_id,
                    event_type="companion.video.failed",
                    payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": job.error},
                )
        await db.commit()
    if jobs:
        logger.info("resumed video generation jobs", extra={"count": len(jobs)})


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
        if pack.status == "processing":
            raise VideoPackStateError("构建中的视频包不能删除，请等待完成")
        manifest = safe_json_loads(pack.manifest_json or "{}", default={})
        if isinstance(manifest, dict):
            for clip in manifest.get("clips", []) or []:
                path = clip.get("path") if isinstance(clip, dict) else None
                if isinstance(path, str) and path:
                    with contextlib.suppress(OSError):
                        unlink_companion_asset(path)
        with contextlib.suppress(OSError):
            unlink_companion_asset(pack.manifest_path)
        # 任务行与源视频随包清理：job.pack_id 外键无级联，任务行必须先删；
        # 生成源视频保留到包删除时才回收。
        jobs = (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == pack_id))).scalars().all()
        for job in jobs:
            for path in (job.artifact_path, job.result_path):
                if path:
                    with contextlib.suppress(OSError):
                        unlink_companion_asset(path)
            await db.delete(job)
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
        resumable_ids = set(
            (
                await db.execute(
                    select(CompanionVideoJob.pack_id).where(
                        CompanionVideoJob.action == FULL_PACK_ACTION,
                        CompanionVideoJob.pack_id.in_([pack.id for pack in packs]),
                        CompanionVideoJob.provider_task_id.is_not(None)
                        | CompanionVideoJob.artifact_path.is_not(None)
                        | CompanionVideoJob.script_json.is_not(None),
                    ),
                )
            ).scalars(),
        )
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
