"""视频动作包编排：外观版本 → 动作片段处理 → 不可变包发布 → 激活。

状态与阶段分离（status × stage）逐任务持久化；FFmpeg 等待走工作线程，
不占数据库长事务。发布与激活共用头像用户级锁：生成包沿用冻结资料完成，
自动激活核对参考和角色卡修订；新包构建失败不清空旧激活包。

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
import json
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
    track_user_task,
    utc_now,
)
from modules.companion import (
    REQUIRED_SYSTEM_SLOTS,
    SYSTEM_SLOTS,
    AvatarAsset,
    CompanionAction,
    CompanionActionPack,
    CompanionOutfit,
)
from modules.ws import emit_ws_event
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from services.domains.actions import fulfill_deferred_play_intents, publish_action_catalog
from services.domains.companion import (
    character_snapshot_is_current,
    get_or_create_persona,
    load_persona_definition,
    render_character_profile,
    require_character_snapshot,
)
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
    VisualReasoningError,
    execute_with_fallback,
    resolve,
    resolve_provider_chain,
    resolve_vision_chain,
)
from services.infrastructure.video_processing import (
    HITMASK_FPS,
    HITMASK_GRID_H,
    HITMASK_GRID_W,
    MAX_CANVAS_HEIGHT,
    MAX_CANVAS_WIDTH,
    MAX_SOURCE_BYTES,
    TARGET_EXT,
    VideoProcessError,
    build_hitmask,
    extract_cover,
    matte_video,
    prepare_action_clip,
    require_matting_model,
    select_full_clip,
    select_loop,
)

from ..avatar_service import get_avatar_job_lock, load_avatar_bytes_as_data_uri
from ..identity_review import review_character_frames
from ..image_generation import ImageGenerationError, generate_images, resolve_image_gen_chain
from ..media_review import create_media_review
from ..visual_identity import align_character_reference, needs_identity_alignment
from .manifest import (
    VideoClipSpec,
)
from .script import (
    SYSTEM_ACTION_SEMANTICS,
    ActionScriptEntry,
    ActionSpec,
    VideoScriptError,
    build_pose_prompt,
    build_video_prompt,
    compose_action_script,
)
from .state import ActionResult, GenerationContext

logger = get_logger(__name__)

CompanionVideoPack = CompanionActionPack
CompanionVideoJob = CompanionAction

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
    if not set(REQUIRED_SYSTEM_SLOTS) <= set(clips) or not set(clips) <= set(SYSTEM_SLOTS):
        raise VideoPackError("片段须包含待机与拖拽，且只能是已知动作")
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
                    key=action,
                    kind="loop",
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
    initial_only: bool = False,
    source_pack_id: int | None = None,
    action: str | None = None,
    feedback: str = "",
) -> CompanionVideoPack:
    """独立 Grok 短动作；单动作请求在 ready 包上原地重做（保持 pack_id，只推进素材版本），
    仅在包尚未 ready 时形成新版本并复用冻结参考。"""
    if (source_pack_id is None) != (action is None):
        raise VideoPackStateError("单动作请求必须指定有效视频包与动作")
    async with get_avatar_job_lock(user_id):
        persona = await get_or_create_persona(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成 onboarding")
        source = await _get_pack(db, user_id, source_pack_id) if source_pack_id is not None else None
        if source_pack_id is not None and (
            source is None or source.status not in ("ready", "failed") or not source.reference_path
        ):
            raise VideoPackStateError("该视频包不能生成该动作")
        # 同一外观快照：ready 包上单动作原地重做，不新建 pack。
        if source is not None and action is not None and source.status == "ready":
            await _reject_concurrent_build(db, user_id)
            job = await _queue_in_place_redo(db, source, action, feedback)
            await db.commit()
            kick_dynamic_action(source.id, job.id, user_id)
            await db.refresh(source)
            return source
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
        if initial_only:
            existing = await db.scalar(
                select(CompanionVideoPack)
                .where(
                    CompanionVideoPack.user_id == user_id,
                    CompanionVideoPack.outfit_id == outfit.id,
                )
                .order_by(CompanionVideoPack.id.desc())
                .limit(1),
            )
            if existing is not None:
                return existing
            if outfit.initial_video_started:
                raise VideoPackStateError("默认视频任务已移除，请在外观页重新生成")
        await _reject_concurrent_build(db, user_id)
        avatar = (
            await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
        ).scalar_one_or_none()
        if source is not None and (avatar is None or source.avatar_id != avatar.id):
            raise VideoPackStateError("角色形象已切换，请生成完整新包")
        reference_hash = source.reference_hash if source is not None else await _reference_hash(outfit, avatar)
        source_context = _load_generation_context(source) if source is not None else None
        if source is not None and source_context is None:
            raise VideoPackStateError("该视频包缺少有效生成上下文，请生成完整新包")
        identity = (
            source_context.identity if source_context is not None else await require_character_snapshot(db, user_id)
        )
        if source_context is not None and source_context.reference_alignment != "ready":
            raise VideoPackStateError("参考图尚未准备完成，请生成完整新包")
        if not force and source is None:
            reusable = (
                await db.execute(
                    select(CompanionVideoPack)
                    .where(
                        CompanionVideoPack.user_id == user_id,
                        CompanionVideoPack.outfit_id == outfit.id,
                        CompanionVideoPack.reference_hash == reference_hash,
                        CompanionVideoPack.status == "ready",
                        CompanionVideoPack.reference_path != "",
                    )
                    .order_by(CompanionVideoPack.pack_version.desc())
                    .limit(1),
                )
            ).scalar_one_or_none()
            reusable_context = _load_generation_context(reusable) if reusable is not None else None
            if (
                reusable is not None
                and reusable_context is not None
                and reusable_context.identity == identity
                and await character_snapshot_is_current(db, user_id, identity)
                and await _newer_ready_pack(db, reusable) is None
            ):
                await _activate_locked(db, reusable)
                retired = await _retire_superseded_locked(db, reusable)
                await db.commit()
                _unlink_assets(retired)
                return reusable
        await _video_providers(user_id)
        if not await resolve_vision_chain(db, user_id):
            raise VideoPackStateError("未配置视觉模型，无法根据角色参考图撰写动作脚本")
        image_chain, image_error = await resolve_image_gen_chain(db, user_id, "reference", image_edit=True)
        if not image_chain:
            raise VideoPackStateError(image_error or "请配置图像编辑供应商以生成动作姿态")
        try:
            require_matting_model()
        except VideoProcessError as exc:
            raise VideoPackStateError(str(exc)) from exc
        previous = {}
        if source is not None:
            previous = {
                job.action: job
                for job in (
                    await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == source.id))
                ).scalars()
            }
            # 必需动作结果未知且不是本次目标时拒绝，避免目标动作付费完成后整包仍无法发布。
            for key in REQUIRED_SYSTEM_SLOTS:
                if key == action:
                    continue
                old = _inheritable_job(previous.get(key))
                if old is not None and old.status != "succeeded" and not _can_resume_job(old):
                    raise VideoPackStateError("请先重做失败的必需动作，再补齐其他动作")
        if source is not None:
            reference_path = source.reference_path
            if not _artifact_abs_path(reference_path).is_file():
                raise VideoPackStateError("该视频包的冻结参考图不可读")
        else:
            reference_uri = await _process_thread(load_avatar_bytes_as_data_uri, outfit.fullbody_url)
            if not reference_uri:
                raise VideoPackStateError("外观参考图不可读")
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
        if source_context is not None:
            action_feedback = dict(source_context.action_feedback)
            if action is not None and feedback.strip():
                action_feedback[action] = feedback.strip()[:1000]
            context = source_context.model_copy(
                update={"action_feedback": action_feedback, "active_outfit_id": active_outfit_id},
            )
        else:
            outfit_source = safe_json_loads(outfit.source_json, default={})
            applied_revision = outfit_source.get("character_card_revision") if isinstance(outfit_source, dict) else None
            context = GenerationContext(
                identity=identity,
                reference_alignment="pending" if needs_identity_alignment(identity, applied_revision) else "ready",
                persona_definition={
                    key: value for key, value in load_persona_definition(persona).items() if key != "appearance"
                },
                personality_tags=safe_json_loads(persona.personality_tags_json or "[]", default=[]),
                outfit_description=outfit.description or "",
                feedback=feedback,
                active_outfit_id=outfit.id if initial_only else active_outfit_id,
            )
        pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
        pack.reference_path = reference_path
        canvas_w, canvas_h = _DEFAULT_CANVAS
        pack.canvas_spec = json.dumps({"width": canvas_w, "height": canvas_h, "fps": 24}, ensure_ascii=False)
        # 冻结评审用快照：角色卡资料与当前着装描述（独立评审判断可行性依据）。
        pack.character_snapshot = json.dumps(
            {"profile": render_character_profile(identity)},
            ensure_ascii=False,
        )
        pack.outfit_snapshot = json.dumps({"description": context.outfit_description}, ensure_ascii=False)
        # 新整包只建必需动作；单动作请求继承源包全部已有动作并补齐或重做目标动作。
        # 成功动作直接复用；可续跑的失败动作保留句柄重新排队；结果未知的非目标非必需动作
        # 保留失败记录且不阻塞其他动作重做。must_actions 持久化本版本必须成功的集合。
        if source is not None:
            action_keys = set(previous) | {action, *REQUIRED_SYSTEM_SLOTS}
        else:
            action_keys = set(REQUIRED_SYSTEM_SLOTS)
        must_actions: set[str] = set(REQUIRED_SYSTEM_SLOTS)
        for key in sorted(action_keys, key=_action_order):
            old = _inheritable_job(previous.get(key)) if key != action else None
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
                for field in (
                    "name",
                    "system_slot",
                    "kind",
                    "motion_description",
                    "use_when",
                    "avoid_when",
                    "tags",
                    "enabled",
                    "metadata_revision",
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
                    "source_design_json",
                    "error",
                    "submitted",
                    "retry_safe",
                    "video_path",
                    "video_hash",
                    "target_duration_seconds",
                    "actual_duration_ms",
                    "frames",
                    "loopable",
                    "cover_path",
                    "hitmask_path",
                    "hitmask_grid_w",
                    "hitmask_grid_h",
                    "hitmask_fps",
                    "enter_pose",
                    "exit_pose",
                ):
                    setattr(job, field, getattr(old, field))
                if job.status == "succeeded":
                    must_actions.add(key)
                elif _can_resume_job(old):
                    job.status, job.error = "queued", None
                    must_actions.add(key)
            else:
                must_actions.add(key)
            db.add(job)
        context = context.model_copy(update={"must_actions": sorted(must_actions, key=_action_order)})
        pack.context_json = context.model_dump_json()
        await db.commit()
        await db.refresh(pack)
    _kick_generate(pack.id, user_id)
    return pack


def _action_order(action: str) -> tuple[int, str]:
    """任务排序键：已知动作按全集聚合顺序，可选动作排在其后并按动作名稳定排序。"""
    return (SYSTEM_SLOTS.index(action) if action in SYSTEM_SLOTS else len(SYSTEM_SLOTS), action)


def _can_resume_job(job: CompanionVideoJob) -> bool:
    if job.status in ("succeeded", "review") or job.stage == "merged":
        return False
    return bool(
        job.provider_task_id or job.artifact_path or job.stage == "script" or (job.stage == "pose" and job.pose_path),
    )


def _inheritable_job(job: CompanionVideoJob | None) -> CompanionVideoJob | None:
    """已迁出续跑入口的任务行不参与继承与再清理。"""
    if job is None or job.stage == "merged":
        return None
    return job


def _must_succeed_actions(jobs: list[CompanionVideoJob], context_must: set[str] | list[str]) -> set[str]:
    """本版本必须成功的动作。must_actions 为空表示旧数据 / 上传包，按全部任务判定。"""
    must = set(context_must)
    if must:
        return must | set(REQUIRED_SYSTEM_SLOTS)
    return {job.action for job in jobs} | set(REQUIRED_SYSTEM_SLOTS)


def _can_publish_jobs(jobs: list[CompanionVideoJob], context_must: set[str] | list[str]) -> bool:
    by_action = {job.action: job for job in jobs}
    for action in _must_succeed_actions(jobs, context_must):
        job = by_action.get(action)
        if job is None or job.status != "succeeded" or not job.result_json:
            return False
    return True


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
        if outfit is None or avatar is None or pack.avatar_id != avatar.id:
            raise VideoPackStateError("该视频包对应的角色形象已切换，请生成完整新包")
        if not _artifact_abs_path(pack.reference_path).is_file():
            raise VideoPackStateError("该视频包的冻结参考图不可读")
        context = _load_generation_context(pack)
        if context is None:
            raise VideoPackStateError("该视频包缺少有效生成上下文，请生成完整新包")
        if context.reference_alignment == "running":
            raise VideoPackStateError("参考图生成结果未知，请核对后生成完整新包")
        jobs = list(
            (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == pack_id))).scalars(),
        )
        newer = await _newer_ready_pack(db, pack)
        if newer is not None and not _same_generation_lineage(pack, newer):
            newer = None
        if newer is not None:
            by_action = {job.action: job for job in jobs}
            newer_jobs = (
                (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == newer.id)))
                .scalars()
                .all()
            )
            for job in newer_jobs:
                # 最新成功结果优先；尚未完成的动作沿用原任务句柄和脚本。
                if job.status == "succeeded" or job.action not in by_action:
                    by_action[job.action] = job
            jobs = sorted(by_action.values(), key=lambda job: _action_order(job.action))
            must_actions = _must_succeed_actions(jobs, context.must_actions)
            must_actions.update(job.action for job in jobs if job.status == "succeeded" or _can_resume_job(job))
            context = context.model_copy(update={"must_actions": sorted(must_actions, key=_action_order)})
        recoverable = [job for job in jobs if _can_resume_job(job)]
        if not recoverable and not _can_publish_jobs(jobs, context.must_actions):
            raise VideoPackStateError("提交结果未知，请核对供应商任务后选择重做动作")
        if newer is not None:
            # 旧包不能原地变成更旧的激活版本；合并结果通过新的不可变版本交付。
            reference_path = pack.reference_path
            pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=pack.reference_hash)
            pack.reference_path = reference_path
            pack.context_json = context.model_dump_json()
            merged = jobs
            jobs = [_copy_job_to_pack(job, pack) for job in merged]
            db.add_all(jobs)
            for original in merged:
                # 续跑入口迁到新版本，避免同一句柄或脚本任务在新旧包重复排队。
                if _can_resume_job(original):
                    original.stage = "merged"
                    original.provider_task_id = None
                    original.error = "已并入新版本"
            recoverable = [job for job in jobs if _can_resume_job(job)]
        for job in recoverable:
            job.status, job.error = "queued", None
        pack.status, pack.error = "processing", None
        await db.commit()
    _kick_generate(pack.id, user_id)
    return pack


async def _newer_ready_pack(db: AsyncSession, pack: CompanionVideoPack) -> CompanionVideoPack | None:
    return await db.scalar(
        select(CompanionVideoPack)
        .where(
            CompanionVideoPack.user_id == pack.user_id,
            CompanionVideoPack.outfit_id == pack.outfit_id,
            CompanionVideoPack.pack_version > pack.pack_version,
            CompanionVideoPack.status == "ready",
        )
        .order_by(CompanionVideoPack.pack_version.desc())
        .limit(1),
    )


async def _insert_pack(
    db: AsyncSession,
    user_id: int,
    *,
    avatar: AvatarAsset | None,
    outfit: CompanionOutfit,
    reference_hash: str,
) -> CompanionVideoPack:
    if outfit.is_initial:
        outfit.initial_video_started = True
        outfit.initial_video_error = None
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
    pack = CompanionVideoPack(
        user_id=user_id,
        avatar_id=avatar.id if avatar is not None else None,
        outfit_id=outfit.id,
        status="processing",
        reference_hash=reference_hash,
    )
    # 版本号 = 同外观历史最大版本 + 1；版本不可覆盖，单动作请求即新版本。
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
) -> tuple[VideoClipSpec, str, str]:
    """处理单个动作片段并转存资产；返回 (clip 描述符, 封面存储路径, 命中遮罩存储路径)。

    封面与命中遮罩从最终交付片段读取，使用 libvpx 解码 VP9 alpha。
    遮罩单独落盘供动作目录 hitmask_ref 引用，播放器可按帧查询命中。"""
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
        hitmask_stored = await save_companion_asset_async(
            json.dumps(hitmask, ensure_ascii=False).encode("utf-8"),
            user_id=user_id,
            label=f"video_{action}_hitmask",
            ext="json",
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
        hitmask_stored,
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
        if job is None or job.status in ("succeeded", "review", "failed"):
            return
        job.status = status
        job.stage = stage
        for key, value in fields.items():
            setattr(job, key, value)
        if job.result_path and not job.video_path:
            job.video_path = job.result_path
        await db.commit()


async def _mark_jobs(
    db: AsyncSession,
    pack_id: int,
    *,
    status: str,
    stage: str | None = None,
    error: str | None = None,
    keys: set[str] | None = None,
) -> None:
    """批量改写包下未终态任务行；keys 非空时只改这些动作键。"""
    values: dict[str, object] = {"status": status, "error": error}
    if stage is not None:
        values["stage"] = stage
    stmt = (
        update(CompanionVideoJob)
        .where(CompanionVideoJob.pack_id == pack_id, CompanionVideoJob.status.not_in(("succeeded", "review", "failed")))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if keys is not None:
        stmt = stmt.where(CompanionVideoJob.key.in_(keys))
    await db.execute(stmt)


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
    """校验并发布不可变资产；生成包的迟到结果保留为历史版本。

    自动激活核对当前参考与角色卡，和 ready 发布在同一事务提交。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None:
            return False
        user_id = pack.user_id
        reference_path = pack.reference_path
        generated = pack.context_json is not None
        if generated:
            context = _load_generation_context(pack)
            avatar = await db.get(AvatarAsset, context.identity.avatar_id) if context is not None else None
            if (
                context is not None
                and avatar is not None
                and avatar.user_id == user_id
                and await character_snapshot_is_current(db, user_id, context.identity)
            ):
                reference_path = avatar.seed_fullbody_url
    if generated and cover_path:
        try:
            reference_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, reference_path)
            frame_uris: list[str] = []
            with tempfile.TemporaryDirectory(prefix="spiritagent-identity-") as directory:
                for spec in clip_specs:
                    if spec.action not in REQUIRED_SYSTEM_SLOTS:
                        continue
                    for position, second in enumerate(
                        (0.0, spec.duration_ms / 2000, max(0.0, spec.duration_ms / 1000 - 0.15)),
                    ):
                        frame_path = Path(directory) / f"{spec.action}-{position}.webp"
                        await _process_thread(
                            extract_cover,
                            _artifact_abs_path(spec.path),
                            frame_path,
                            canvas_w=canvas[0],
                            canvas_h=canvas[1],
                            at_seconds=second,
                        )
                        frame_uris.append(await _process_thread(_image_data_uri, frame_path))
            review_status, review_reason = await review_character_frames(
                user_id,
                reference_uri,
                tuple(frame_uris),
                pack_wide=True,
            )
        except Exception:
            logger.warning("video pack identity review failed", extra={"pack_id": pack_id}, exc_info=True)
            review_status, review_reason = "review", "视频形象的自动检查未完成，请预览后手动启用"
    else:
        review_status, review_reason = ("review", "视频封面不可用，请预览后手动启用") if generated else ("accepted", "")
    async with get_avatar_job_lock(user_id):
        return await _publish_ready_locked(
            pack_id,
            canvas=canvas,
            clip_specs=clip_specs,
            cover_path=cover_path,
            auto_activate=auto_activate,
            identity_review=review_status,
            identity_review_reason=review_reason,
        )


async def _publish_catalog_in_session(db: AsyncSession, pack: CompanionVideoPack) -> int | None:
    """从任务行聚合发布动作目录（spiritagent.action.pack），CAS 推进。

    目录即当前视频包的可播动作清单。必需系统槽位不齐时跳过（不发布、不破坏现有目录）。
    返回新版本号或 None。
    """
    assets_dir = Path(SETTINGS.data_dir) / "companion-assets" / str(pack.user_id)
    assets_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await publish_action_catalog(db, pack, assets_dir=assets_dir)
    except Exception:  # noqa: BLE001 — 目录发布失败不回滚素材；ready 状态与事件照常提交
        logger.exception("action catalog publish failed", extra={"pack_id": pack.id})
        return None


async def _publish_ready_locked(
    pack_id: int,
    *,
    canvas: tuple[int, int],  # noqa: ARG001 — 画布写入 pack.canvas_spec；发布目录时由任务行聚合
    clip_specs: list[VideoClipSpec],
    cover_path: str | None,
    auto_activate: bool,
    identity_review: str,
    identity_review_reason: str,
) -> bool:
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None or pack.status != "processing":
            return False

        outfit = await db.get(CompanionOutfit, pack.outfit_id) if pack.outfit_id is not None else None
        avatar = (
            await db.execute(
                select(AvatarAsset).where(AvatarAsset.user_id == pack.user_id, AvatarAsset.active.is_(True)),
            )
        ).scalar_one_or_none()
        reference_is_current = outfit is not None and await _reference_hash(outfit, avatar) == pack.reference_hash
        if not reference_is_current and not pack.reference_path:
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

    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None or pack.status != "processing":
            return False
        ready_keys = {spec.action for spec in clip_specs}
        missing = [slot for slot in REQUIRED_SYSTEM_SLOTS if slot not in ready_keys]
        if missing:
            pack.status = "failed"
            pack.error = "缺少必需系统动作：" + "、".join(missing)
            await _mark_jobs(db, pack.id, status="failed", stage="publish", error=pack.error)
            emit_ws_event(
                db,
                user_id=pack.user_id,
                event_type="companion.video.failed",
                payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": pack.error},
            )
            await db.commit()
            return False
        pack.status = "ready"
        pack.error = None
        pack.identity_review = identity_review
        pack.identity_review_reason = identity_review_reason
        if cover_path:
            pack.manifest_json = json.dumps({"cover_path": cover_path}, ensure_ascii=False)
        # 仅将本次进入目录的任务标为成功；未产出素材的任务不冒充成功。
        await _mark_jobs(
            db,
            pack.id,
            status="succeeded",
            stage="publish",
            keys={spec.action for spec in clip_specs},
        )
        # 任务行规格回写：实际时长/帧数/loopable 供目录发布消费。
        job_rows = (
            (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == pack_id))).scalars().all()
        )
        spec_by_key = {spec.action: spec for spec in clip_specs}
        for job_row in job_rows:
            spec = spec_by_key.get(job_row.key)
            if spec is None:
                continue
            job_row.video_path = spec.path
            job_row.video_hash = spec.sha256
            job_row.actual_duration_ms = spec.duration_ms
            job_row.frames = spec.frames
            # loopable 按动作行自身 kind：once 动作（如完整舞蹈）不标记可循环。
            job_row.loopable = job_row.kind == "loop"
        # 动作目录 = 当前视频包可播清单：从任务行聚合发布。
        await _publish_catalog_in_session(db, pack)
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.video.ready",
            payload={"packId": pack.id, "outfitId": pack.outfit_id, "packVersion": pack.pack_version},
        )
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.action.catalog_changed",
            payload={"packId": pack.id, "catalogVersion": pack.catalog_version},
        )
        retired: set[str] = set()
        if (
            auto_activate
            and identity_review in ("pass", "accepted")
            and reference_is_current
            and await _newer_ready_pack(db, pack) is None
        ):
            context = GenerationContext.model_validate_json(pack.context_json)
            active_outfit_id = (
                await db.execute(
                    select(CompanionOutfit.id).where(
                        CompanionOutfit.user_id == pack.user_id,
                        CompanionOutfit.active.is_(True),
                    ),
                )
            ).scalar_one_or_none()
            if active_outfit_id in (context.active_outfit_id, pack.outfit_id) and await character_snapshot_is_current(
                db,
                pack.user_id,
                context.identity,
            ):
                await _activate_locked(db, pack)
                retired = await _retire_superseded_locked(db, pack)
        await db.commit()
    _unlink_assets(retired)
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
                spec, cover_stored, hitmask_stored = await _prepare_clip_spec(
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
                        .where(CompanionVideoJob.pack_id == pack_id, CompanionVideoJob.key == action)
                        .values(
                            status="succeeded",
                            stage="publish",
                            result_path=spec.path,
                            video_path=spec.path,
                            video_hash=spec.sha256,
                            actual_duration_ms=spec.duration_ms,
                            frames=spec.frames,
                            cover_path=cover_stored,
                            hitmask_path=hitmask_stored,
                            hitmask_grid_w=HITMASK_GRID_W,
                            hitmask_grid_h=HITMASK_GRID_H,
                            hitmask_fps=HITMASK_FPS,
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
    except (VideoProcessError, VideoPackError, OSError) as exc:
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
        and all(duration in (provider.durations or ()) for duration in (1, 2, 4, 6, 8, 10))
    ]
    if not providers:
        raise VideoPackStateError("请配置支持首尾帧的 Grok 1.5 视频供应商；短动作不会回退到 MiniMax")
    return providers


async def _prepare_pack_identity(pack: CompanionVideoPack, context: GenerationContext) -> GenerationContext:
    if context.reference_alignment == "ready":
        return context
    if context.reference_alignment == "running":
        raise VideoPackStateError("参考图生成结果未知，请核对后生成完整新包")
    async with SESSION_LOCAL() as db:
        avatar = await db.get(AvatarAsset, context.identity.avatar_id)
        current_identity = await character_snapshot_is_current(db, pack.user_id, context.identity)
        seed_path = avatar.seed_fullbody_url if avatar is not None and avatar.user_id == pack.user_id else None
    source = (
        await asyncio.to_thread(load_avatar_bytes_as_data_uri, seed_path)
        if current_identity
        else await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))
    )
    if not source:
        raise VideoPackStateError("当前全身形象无法读取，请先修复参考图")
    context.reference_alignment = "running"
    async with SESSION_LOCAL() as db:
        row = await db.get(CompanionVideoPack, pack.id)
        if row is None or row.status != "processing":
            raise VideoPackStateError("视频任务已失效")
        row.context_json = context.model_dump_json()
        await db.commit()
    try:
        path = await align_character_reference(pack.user_id, source, context.identity, context.outfit_description)
    except ImageGenerationError as exc:
        if not exc.result_unknown:
            async with SESSION_LOCAL() as db:
                row = await db.get(CompanionVideoPack, pack.id)
                if row is not None and row.status == "processing":
                    context.reference_alignment = "pending"
                    row.context_json = context.model_dump_json()
                    await db.commit()
        raise
    previous_path = pack.reference_path
    try:
        async with SESSION_LOCAL() as db:
            row = await db.get(CompanionVideoPack, pack.id)
            if row is None or row.status != "processing":
                raise VideoPackStateError("视频任务已失效")
            context.reference_alignment = "ready"
            row.reference_path = path
            row.context_json = context.model_dump_json()
            await db.commit()
    except BaseException:
        unlink_companion_asset(path)
        raise
    pack.reference_path = path
    pack.context_json = context.model_dump_json()
    # 只有新整包进入此阶段，旧包的共享参考不在此处回收。
    unlink_companion_asset(previous_path)
    return context


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
        context = await _prepare_pack_identity(pack, context)
        must_actions = _must_succeed_actions(jobs, context.must_actions)
        pending = [job for job in jobs if job.status != "succeeded" and job.status != "failed" and not job.script_json]
        if pending:
            await _emit_pack_event(pack.user_id, "companion.video.progress", {"packId": pack_id, "stage": "script"})
            specs = []
            for job in pending:
                if job.action in SYSTEM_ACTION_SEMANTICS:
                    # 系统槽位固定语义；待机一秒短循环，其余系统动作两秒。
                    idle = job.action == "idle"
                    specs.append(
                        ActionSpec(
                            action=job.action,
                            system_slot=job.action,
                            duration_seconds=1.0 if idle else 2.0,
                            clip_kind="loop",
                        ),
                    )
                    continue
                # 动态动作：规格冻结在动作行（评审 approve 时写入）。
                design = safe_json_loads(job.source_design_json or "{}", default={})
                if design:
                    specs.append(
                        ActionSpec(
                            action=job.action,
                            system_slot="",
                            name=str(design.get("name", job.action)),
                            semantics=str(design.get("motion_description", "")),
                            use_when=list(design.get("use_when") or []),
                            avoid_when=list(design.get("avoid_when") or []),
                            duration_seconds=float(design.get("duration_seconds", 2.0) or 2.0),
                            clip_kind=str(design.get("clip_kind", "once") or "once"),
                        ),
                    )
                else:
                    specs.append(
                        ActionSpec(
                            action=job.action,
                            duration_seconds=float(job.target_duration_seconds or 2.0),
                            clip_kind=str(job.kind or "once"),
                        ),
                    )
            for spec in specs:
                spec.feedback = context.action_feedback.get(spec.action, "")
            script = await compose_action_script(
                pack.user_id,
                reference_image=await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path)),
                identity=context.identity,
                persona_definition=context.persona_definition,
                personality_tags=context.personality_tags,
                outfit_description=context.outfit_description,
                specs=specs,
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
            if job.status in ("succeeded", "failed", "review"):
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
        # 阻塞发布：must_actions（必需 + 本版本必须成功）未完成。继承的其他未知失败不阻塞。
        blocking = [
            job for job in results if (job.status != "succeeded" or not job.result_json) and job.action in must_actions
        ]
        if blocking:
            await _fail_pack(
                pack_id,
                VideoPackError("；".join(f"{job.action}: {job.error or '动作未完成'}" for job in blocking)),
            )
            return
        specs: list[VideoClipSpec] = []
        cover_path = None
        for job in sorted(results, key=lambda job: _action_order(job.action)):
            if job.status != "succeeded" or not job.result_json:
                continue
            result = ActionResult.model_validate_json(job.result_json)
            specs.append(result.clip)
            if job.action == "idle":
                cover_path = result.cover_path
        # 同血缘其他版本已成功、本包缺失的动作并入清单，避免恢复旧版本时丢掉新成果。
        have = {clip.action for clip in specs}
        for action, result in await _sibling_success_results(pack):
            if action in have:
                continue
            specs.append(result.clip)
            have.add(action)
            if action == "idle" and cover_path is None:
                cover_path = result.cover_path
        specs.sort(key=lambda clip: _action_order(clip.action))
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
    if isinstance(
        exc,
        (VideoProcessError, VideoPackError, VideoScriptError, ImageGenerationError, VisualReasoningError),
    ):
        return str(exc)[:500]
    return "动作处理失败，可重试已有任务或素材"


async def _generate_action(pack: CompanionVideoPack, job: CompanionVideoJob) -> None:
    """整包生成路径：脚本已由 _generate_pack 预写（含规格），直接续管线。"""
    entry = ActionScriptEntry.model_validate_json(job.script_json or "{}")
    context = GenerationContext.model_validate_json(pack.context_json)
    await _run_action_pipeline(pack, job, entry, context)


async def _run_action_pipeline(
    pack: CompanionVideoPack,
    job: CompanionVideoJob,
    entry: ActionScriptEntry,
    context: GenerationContext,
) -> None:
    """姿态 → 提交 → 轮询 → 下载 → 抠像 → 验收 → 落库；整包与动态动作共用。"""

    async def progress(stage: str) -> None:
        job.stage = stage
        await _advance_job(job.id, stage=stage)
        await _emit_pack_event(
            pack.user_id,
            "companion.video.progress",
            {"packId": pack.id, "stage": stage, "action": job.action},
        )

    if not job.artifact_path:
        if not job.provider_task_id:
            providers = await _video_providers(pack.user_id)
            if job.stage == "submit":
                raise VideoPackError("提交结果未知，未自动重发；请核对供应商任务")
            reference_uri = await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))
            if not job.pose_path:
                if job.action == "idle":
                    job.pose_path = pack.reference_path
                else:
                    if job.stage == "pose":
                        raise VideoPackError("动作姿态生成结果未知，请核对后重做此动作")
                    await progress("pose")
                    paths = await generate_images(
                        build_pose_prompt(entry, context.identity),
                        user_id=pack.user_id,
                        reference_image=reference_uri,
                        size="1024x1792",
                        image_edit=True,
                        persist_user_assets=True,
                    )
                    job.pose_path = paths[0]
                await _advance_job(job.id, stage="pose", pose_path=job.pose_path)
            pose_uri = (
                reference_uri
                if job.pose_path == pack.reference_path
                else await _process_thread(_image_data_uri, _artifact_abs_path(job.pose_path))
            )
            submitted: dict[str, str] = {}

            async def submit(provider: VideoGenProvider) -> VideoJobStatus:
                submitted.update(provider=provider.provider_name, model=provider.config.model)
                # once 动作只约束身份与起始状态，不强制首尾同帧；
                # loop 动作首末帧锚定同一姿态关键帧。
                anchor_last = entry.clip_kind == "loop"
                return await provider.submit(
                    VideoGenRequest(
                        prompt=build_video_prompt(entry, context.identity),
                        duration=entry.duration_seconds,
                        resolution="720p",
                        first_frame_image=pose_uri,
                        last_frame_image=pose_uri if anchor_last else None,
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
        if entry.clip_kind == "loop":
            loop = await _process_thread(select_loop, matte, max_seconds=entry.duration_seconds)
        else:
            # once 动作保留完整时间轴（准备、主体、结束），不裁成短循环。
            loop = await _process_thread(select_full_clip, matte, max_seconds=entry.duration_seconds)
        spec, cover, hitmask_path = await _prepare_clip_spec(
            work,
            job.action,
            matte,
            pack.user_id,
            *_DEFAULT_CANVAS,
            start=loop.start,
            end=loop.end,
        )
        review_id = None
        if pack.status == "ready":
            try:
                reference_uri = await _process_thread(load_avatar_bytes_as_data_uri, pack.reference_path)
                frame_uris: list[str] = []
                for index, second in enumerate(
                    (0.0, spec.duration_ms / 2000, max(0.0, spec.duration_ms / 1000 - 0.15)),
                ):
                    frame = work / f"identity-{index}.webp"
                    await _process_thread(
                        extract_cover,
                        _artifact_abs_path(spec.path),
                        frame,
                        canvas_w=_DEFAULT_CANVAS[0],
                        canvas_h=_DEFAULT_CANVAS[1],
                        at_seconds=second,
                    )
                    frame_uris.append(await _process_thread(_image_data_uri, frame))
                verdict, reason = await review_character_frames(pack.user_id, reference_uri, tuple(frame_uris))
            except Exception:
                logger.warning("dynamic action identity review failed", extra={"action_id": job.id}, exc_info=True)
                verdict, reason = "review", "动作视频的自动检查未完成，请预览确认"
            if verdict == "review":
                review_id = await create_media_review(
                    pack.user_id,
                    "video",
                    spec.path,
                    reason,
                    publication={
                        "kind": "action",
                        "pack_id": pack.id,
                        "action_id": job.id,
                        "title": job.name or job.action,
                    },
                )
        await _advance_job(
            job.id,
            stage="publish",
            status="review" if review_id else "succeeded",
            result_path=spec.path,
            result_json=ActionResult(clip=spec, cover_path=cover, quality=loop).model_dump_json(),
            video_path=spec.path,
            video_hash=spec.sha256,
            actual_duration_ms=spec.duration_ms,
            frames=spec.frames,
            loopable=entry.clip_kind == "loop",
            cover_path=cover,
            hitmask_path=hitmask_path,
            hitmask_grid_w=HITMASK_GRID_W,
            hitmask_grid_h=HITMASK_GRID_H,
            hitmask_fps=HITMASK_FPS,
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


def _kick_generate(pack_id: int, user_id: int) -> None:
    if pack_id in _GEN_INFLIGHT:
        return
    task = asyncio.create_task(_generate_pack(pack_id), name=f"companion.video.generate.{pack_id}")
    _GEN_TASKS.add(task)
    _GEN_INFLIGHT.add(pack_id)
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def _done(_task: asyncio.Task[None]) -> None:
        _GEN_TASKS.discard(_task)
        _GEN_INFLIGHT.discard(pack_id)

    task.add_done_callback(_done)


async def _dynamic_action_spec(job: CompanionVideoJob) -> ActionSpec | None:
    """动作规格：动态动作来自提案设计 JSON；系统槽位用固定语义与时长。"""
    design = safe_json_loads(job.source_design_json or "{}", default={})
    if not design and not job.system_slot:
        return None
    slot = job.system_slot or ""
    if slot and not design:
        return ActionSpec(
            action=job.action,
            system_slot=slot,
            duration_seconds=1.0 if slot == "idle" else 2.0,
            clip_kind="loop",
        )
    return ActionSpec(
        action=job.action,
        system_slot=slot,
        name=str(design.get("name", job.name or job.action)),
        semantics=str(design.get("motion_description", job.motion_description or "")),
        use_when=list(design.get("use_when") or []),
        avoid_when=list(design.get("avoid_when") or []),
        duration_seconds=float(design.get("duration_seconds", job.target_duration_seconds or 2.0) or 2.0),
        clip_kind=str(design.get("clip_kind", job.kind or "once") or "once"),
    )


async def _queue_in_place_redo(
    db: AsyncSession,
    pack: CompanionVideoPack,
    action: str,
    feedback: str,
) -> CompanionVideoJob:
    """ready 包上单动作原地重做：保留动作身份与元数据，只重置素材生成状态。"""
    job = (
        await db.execute(
            select(CompanionVideoJob).where(CompanionVideoJob.pack_id == pack.id, CompanionVideoJob.key == action),
        )
    ).scalar_one_or_none()
    if job is None:
        job = CompanionVideoJob(
            user_id=pack.user_id,
            pack_id=pack.id,
            outfit_id=pack.outfit_id,
            key=action,
            system_slot=action if action in SYSTEM_SLOTS else "",
            name=action,
            kind="loop" if action in SYSTEM_SLOTS else "once",
            status="queued",
            stage="design",
            reference_hash=pack.reference_hash,
        )
        db.add(job)
    else:
        job.status = "queued"
        job.stage = "design"
        job.error = None
        job.provider_task_id = None
        job.artifact_path = None
        job.pose_path = None
        job.result_path = None
        job.result_json = None
        job.video_path = ""
        job.video_hash = ""
        job.cover_path = None
        job.hitmask_path = None
        job.metadata_revision += 1
    if feedback.strip() and pack.context_json:
        context = safe_json_loads(pack.context_json, default={})
        if isinstance(context, dict):
            context.setdefault("action_feedback", {})[action] = feedback.strip()[:1000]
            pack.context_json = json.dumps(context, ensure_ascii=False)
    await db.flush()
    return job


def kick_dynamic_action(pack_id: int, action_id: int, user_id: int) -> None:
    """approve 后启动动态动作生成：向当前 pack 追加单动作任务并异步制作。

    与整包生成共用 _GEN_INFLIGHT 防重；动作生成直接挂在 ready 包上，
    完成后发布新目录快照（catalog_version+1），不新建 pack。
    同包已有生成在途时只登记排队，由在途任务收尾时继续扫描。
    """
    if pack_id in _GEN_INFLIGHT:
        return
    task = asyncio.create_task(
        _generate_dynamic_actions(pack_id, action_ids=[action_id]),
        name=f"companion.video.dynamic.{pack_id}.{action_id}",
    )
    _GEN_TASKS.add(task)
    _GEN_INFLIGHT.add(pack_id)
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def _done(_task: asyncio.Task[None]) -> None:
        _GEN_TASKS.discard(_task)
        _GEN_INFLIGHT.discard(pack_id)
        # 收尾后继续消费排队动作：A 生成期间批准的 B 不会永远停在 queued。
        if not _task.cancelled() and _task.exception() is None:
            _kick_pending_dynamic(pack_id, user_id)

    task.add_done_callback(_done)


def _kick_pending_dynamic(pack_id: int, user_id: int) -> None:
    """扫描并启动该包上仍 queued 的动态动作（不限 action_ids）。"""
    if pack_id in _GEN_INFLIGHT:
        return
    task = asyncio.create_task(
        _generate_dynamic_actions(pack_id),
        name=f"companion.video.dynamic.pending.{pack_id}",
    )
    _GEN_TASKS.add(task)
    _GEN_INFLIGHT.add(pack_id)
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def _done(_task: asyncio.Task[None]) -> None:
        _GEN_TASKS.discard(_task)
        _GEN_INFLIGHT.discard(pack_id)
        if not _task.cancelled() and _task.exception() is None:
            _kick_pending_dynamic(pack_id, user_id)

    task.add_done_callback(_done)


async def _generate_dynamic_actions(pack_id: int, *, action_ids: list[int] | None = None) -> None:
    """ready 包上动态动作的生成编排：规格来自提案设计，逐动作制作后发布新目录。"""
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionVideoPack, pack_id)
            if pack is None or pack.status != "ready":
                return
            stmt = select(CompanionVideoJob).where(
                CompanionVideoJob.pack_id == pack_id,
                CompanionVideoJob.status.in_(("queued", "processing", "result_unknown")),
            )
            if action_ids is not None:
                stmt = stmt.where(CompanionVideoJob.id.in_(action_ids))
            jobs = (await db.execute(stmt.order_by(CompanionVideoJob.id))).scalars().all()
            if not jobs:
                return
        context = GenerationContext.model_validate_json(pack.context_json) if pack.context_json else None
        if context is None:
            await _fail_dynamic_jobs(pack_id, "生成上下文缺失，请重做该动作", action_ids=action_ids)
            return
        for job in jobs:
            try:
                await _generate_one_dynamic(pack, job, context)
            except Exception as exc:  # noqa: BLE001 — 单动作失败隔离，不影响已就绪目录
                logger.exception("dynamic action failed", extra={"pack_id": pack_id, "action_id": job.id})
                await _advance_job(job.id, stage=job.stage, status="failed", error=_generation_error(exc))
        # 全部目标动作处理后发布新目录快照（失败动作不并入），并兑现未过期表达意图。
        await _publish_dynamic_catalog(pack_id)
        await _fulfill_pending_intents(pack_id)
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底：失败必须落库可见
        logger.exception("dynamic action generation crashed", extra={"pack_id": pack_id})
        await _fail_dynamic_jobs(pack_id, _generation_error(exc), action_ids=action_ids)


async def _generate_one_dynamic(
    pack: CompanionVideoPack,
    job: CompanionVideoJob,
    context: GenerationContext,
) -> None:
    """单个动态动作：脚本（按提案规格）→ 姿态 → 提交 → 轮询 → 下载 → 抠像 → 落库。"""

    async def progress(stage: str) -> None:
        job.stage = stage
        await _advance_job(job.id, stage=stage)
        await _emit_pack_event(
            pack.user_id,
            "companion.action.job_updated",
            {"packId": pack.id, "actionId": job.id, "stage": stage},
        )

    spec = await _dynamic_action_spec(job)
    if spec is None:
        raise VideoPackError("动态动作缺少提案规格，请重做")

    entry = await _compose_single_action_script(pack, spec, context)
    job.script_json = entry.model_dump_json()
    async with SESSION_LOCAL() as db:
        await db.execute(
            update(CompanionVideoJob).where(CompanionVideoJob.id == job.id).values(script_json=job.script_json),
        )
        await db.commit()
    await _run_action_pipeline(pack, job, entry, context)


async def _compose_single_action_script(
    pack: CompanionVideoPack,
    spec: ActionSpec,
    context: GenerationContext,
) -> ActionScriptEntry:
    await _emit_pack_event(pack.user_id, "companion.action.job_updated", {"packId": pack.id, "stage": "script"})
    spec = spec.model_copy(update={"feedback": context.action_feedback.get(spec.action, "")})
    script = await compose_action_script(
        pack.user_id,
        reference_image=await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path)),
        identity=context.identity,
        persona_definition=context.persona_definition,
        personality_tags=context.personality_tags,
        outfit_description=context.outfit_description,
        specs=[spec],
        feedback=context.feedback,
    )
    return script.actions[0]


async def _fail_dynamic_jobs(pack_id: int, message: str, *, action_ids: list[int] | None = None) -> None:
    async with SESSION_LOCAL() as db:
        stmt = (
            update(CompanionVideoJob)
            .where(
                CompanionVideoJob.pack_id == pack_id,
                CompanionVideoJob.status == "queued",
            )
            .values(status="failed", error=message[:500])
            .execution_options(synchronize_session=False)
        )
        if action_ids is not None:
            stmt = stmt.where(CompanionVideoJob.id.in_(action_ids))
        await db.execute(stmt)
        await db.commit()


async def _publish_dynamic_catalog(pack_id: int) -> None:
    """动态动作完成后发布动作目录快照（CAS），并广播目录变更。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None:
            return
        version = await _try_publish_catalog(db, pack)
        if version is None:
            return
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.action.catalog_changed",
            payload={"packId": pack.id, "catalogVersion": version},
        )
        await db.commit()


async def _try_publish_catalog(db: AsyncSession, pack: CompanionVideoPack) -> int | None:
    """发布动作目录；失败保留素材与版本行，可通过再次发布恢复。"""
    assets_dir = Path(SETTINGS.data_dir) / "companion-assets" / str(pack.user_id)
    assets_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await publish_action_catalog(db, pack, assets_dir=assets_dir)
    except Exception:  # noqa: BLE001 — 发布失败不毁掉已成功素材，保留重试入口
        logger.exception("dynamic catalog publish failed", extra={"pack_id": pack.id})
        return None


async def republish_action_catalog(user_id: int, pack_id: int) -> int | None:
    """目录发布失败后的恢复入口：仅重发快照，不重新付费生成。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionVideoPack, pack_id)
        if pack is None or pack.user_id != user_id:
            return None
        version = await _try_publish_catalog(db, pack)
        if version is not None:
            emit_ws_event(
                db,
                user_id=user_id,
                event_type="companion.action.catalog_changed",
                payload={"packId": pack.id, "catalogVersion": version},
            )
        await db.commit()
        return version


async def _fulfill_pending_intents(pack_id: int) -> None:
    """动作就绪后补发仍在有效期内的表达意图。"""
    async with SESSION_LOCAL() as db:
        jobs = (
            (
                await db.execute(
                    select(CompanionVideoJob).where(
                        CompanionVideoJob.pack_id == pack_id,
                        CompanionVideoJob.status == "succeeded",
                    ),
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            await fulfill_deferred_play_intents(db, job.id)
        await db.commit()


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
        # ready 包上仍有 queued/processing 动态动作（含已取得供应商句柄的）：逐包恢复生成。
        dynamic_rows = (
            await db.execute(
                select(CompanionVideoPack.id, CompanionVideoPack.user_id)
                .join(CompanionVideoJob, CompanionVideoJob.pack_id == CompanionVideoPack.id)
                .where(
                    CompanionVideoPack.status == "ready",
                    CompanionVideoJob.status.in_(("queued", "processing", "result_unknown")),
                )
                .group_by(CompanionVideoPack.id, CompanionVideoPack.user_id),
            )
        ).all()
    for pack in packs:
        _kick_generate(pack.id, pack.user_id)
    for pack_id, user_id in dynamic_rows:
        _kick_dynamic_resume(pack_id, user_id)


def _kick_dynamic_resume(pack_id: int, user_id: int) -> None:
    """恢复 ready 包上的全部 queued 动态动作（不限 action_ids）。"""
    if pack_id in _GEN_INFLIGHT:
        return

    task = asyncio.create_task(
        _generate_dynamic_actions(pack_id),
        name=f"companion.video.dynamic.resume.{pack_id}",
    )
    _GEN_TASKS.add(task)
    _GEN_INFLIGHT.add(pack_id)
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def _done(_task: asyncio.Task[None]) -> None:
        _GEN_TASKS.discard(_task)
        _GEN_INFLIGHT.discard(pack_id)

    task.add_done_callback(_done)


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
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.action.catalog_changed",
        payload={"packId": pack.id, "catalogVersion": pack.catalog_version},
    )


async def activate_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionVideoPack:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError("找不到视频包")
        if pack.status != "ready":
            raise VideoPackStateError("视频包尚未就绪")
        if await _newer_ready_pack(db, pack) is not None:
            raise VideoPackStateError("该外观已有更新的视频包，请启用最新版本")
        outfit = await db.get(CompanionOutfit, pack.outfit_id)
        avatar = (
            await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
        ).scalar_one_or_none()
        if outfit is None or await _reference_hash(outfit, avatar) != pack.reference_hash:
            raise VideoPackStateError("该视频包对应的参考已变更")
        context = _load_generation_context(pack)
        if context is not None and not await character_snapshot_is_current(db, user_id, context.identity):
            raise VideoPackStateError("视频形象使用旧身体资料，请先制作新视频包")
        if pack.identity_review == "review":
            pack.identity_review = "accepted"
        await _activate_locked(db, pack)
        retired = await _retire_superseded_locked(db, pack)
        await db.commit()
        await db.refresh(pack)
        _unlink_assets(retired)
    return pack


def _pack_assets(pack: CompanionVideoPack, jobs: list[CompanionVideoJob]) -> set[str]:
    paths = {pack.reference_path, pack.manifest_path}
    cover = safe_json_loads(pack.manifest_json or "{}", default={})
    if isinstance(cover, dict) and cover.get("cover_path"):
        paths.add(str(cover["cover_path"]))
    for job in jobs:
        paths.update(
            (
                job.artifact_path or "",
                job.result_path or "",
                job.pose_path or "",
                job.video_path or "",
                job.cover_path or "",
                job.hitmask_path or "",
            ),
        )
        if job.result_json:
            result = ActionResult.model_validate_json(job.result_json)
            paths.update((result.clip.path, result.cover_path))
    return paths - {""}


async def _remove_packs(db: AsyncSession, user_id: int, targets: list[CompanionVideoPack]) -> set[str]:
    """删除目标包及其任务行（调用方负责提交）；返回不再被其余包引用、可回收的资产路径。
    单动作版本间共享资源（冻结参考、复用片段），只有最后一个引用消失才回收。"""
    packs = (await db.execute(select(CompanionVideoPack).where(CompanionVideoPack.user_id == user_id))).scalars().all()
    jobs = (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.user_id == user_id))).scalars().all()
    target_ids = {pack.id for pack in targets}
    jobs_by_pack: dict[int, list[CompanionVideoJob]] = {}
    for job in jobs:
        if job.pack_id is not None:
            jobs_by_pack.setdefault(job.pack_id, []).append(job)
    candidates: set[str] = set()
    for pack in targets:
        candidates |= _pack_assets(pack, jobs_by_pack.get(pack.id, []))
    for other in packs:
        if other.id not in target_ids:
            candidates -= _pack_assets(other, jobs_by_pack.get(other.id, []))
    for pack in targets:
        for job in jobs_by_pack.get(pack.id, []):
            await db.delete(job)
        await db.delete(pack)
    return candidates


def _load_generation_context(pack: CompanionVideoPack) -> GenerationContext | None:
    """解析生成包上下文；上传包 context_json 为占位，返回 None。"""
    raw = (pack.context_json or "").strip()
    if not raw or raw == "{}":
        return None
    try:
        return GenerationContext.model_validate_json(raw)
    except ValidationError:
        return None


def _same_generation_lineage(kept: CompanionVideoPack, other: CompanionVideoPack) -> bool:
    """迁移前核对冻结参考与角色身份，避免旧身份句柄混入新包。"""
    if (
        not kept.reference_path
        or kept.reference_path != other.reference_path
        or not kept.reference_hash
        or kept.reference_hash != other.reference_hash
    ):
        return False
    kept_ctx = _load_generation_context(kept)
    other_ctx = _load_generation_context(other)
    return kept_ctx is not None and other_ctx is not None and kept_ctx.identity == other_ctx.identity


def _copy_job_to_pack(job: CompanionVideoJob, pack: CompanionVideoPack) -> CompanionVideoJob:
    return CompanionVideoJob(
        user_id=job.user_id,
        pack_id=pack.id,
        outfit_id=job.outfit_id,
        key=job.key,
        name=job.name,
        system_slot=job.system_slot,
        kind=job.kind,
        motion_description=job.motion_description,
        use_when=job.use_when,
        avoid_when=job.avoid_when,
        tags=job.tags,
        enabled=job.enabled,
        metadata_revision=job.metadata_revision,
        status=job.status,
        stage=job.stage,
        provider=job.provider,
        model=job.model,
        provider_task_id=job.provider_task_id,
        reference_hash=job.reference_hash,
        input_hash=job.input_hash,
        script_json=job.script_json,
        artifact_path=job.artifact_path,
        pose_path=job.pose_path,
        result_json=job.result_json,
        result_path=job.result_path,
        source_design_json=job.source_design_json,
        error=job.error,
        submitted=job.submitted,
        retry_safe=job.retry_safe,
        video_path=job.video_path,
        video_hash=job.video_hash,
        target_duration_seconds=job.target_duration_seconds,
        actual_duration_ms=job.actual_duration_ms,
        frames=job.frames,
        loopable=job.loopable,
        cover_path=job.cover_path,
        hitmask_path=job.hitmask_path,
        hitmask_grid_w=job.hitmask_grid_w,
        hitmask_grid_h=job.hitmask_grid_h,
        hitmask_fps=job.hitmask_fps,
        enter_pose=job.enter_pose,
        exit_pose=job.exit_pose,
    )


async def _sibling_success_results(pack: CompanionVideoPack) -> list[tuple[str, ActionResult]]:
    """同外观同血缘其他包上已成功的动作结果（用于补齐本包缺失片段）。"""
    async with SESSION_LOCAL() as db:
        rows = (
            await db.execute(
                select(CompanionVideoJob, CompanionVideoPack)
                .join(CompanionVideoPack, CompanionVideoPack.id == CompanionVideoJob.pack_id)
                .where(
                    CompanionVideoPack.user_id == pack.user_id,
                    CompanionVideoPack.outfit_id == pack.outfit_id,
                    CompanionVideoPack.id != pack.id,
                    CompanionVideoJob.status == "succeeded",
                    CompanionVideoJob.result_json.is_not(None),
                )
                # 同动作多版本成功时取最新，与「冲突动作优先保留最新成功结果」一致。
                .order_by(CompanionVideoPack.pack_version.desc(), CompanionVideoJob.id.desc()),
            )
        ).all()
    found: list[tuple[str, ActionResult]] = []
    seen: set[str] = set()
    for job, other in rows:
        if job.action in seen or not _same_generation_lineage(pack, other):
            continue
        if job.reference_hash and pack.reference_hash and job.reference_hash != pack.reference_hash:
            continue
        seen.add(job.action)
        found.append((job.action, ActionResult.model_validate_json(job.result_json)))
    return found


async def _carry_incomplete_jobs(db: AsyncSession, kept: CompanionVideoPack, targets: list[CompanionVideoPack]) -> None:
    """清理前把将删除包上的有效记录迁到 kept：不可续跑失败记录，以及 kept 缺失的成功结果。
    可续跑任务不迁入（保留源包作续跑入口）；血缘不一致的任务不迁入。"""
    target_ids = {pack.id for pack in targets if _same_generation_lineage(kept, pack)}
    if not target_ids:
        return
    kept_jobs = {
        job.action: job
        for job in (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == kept.id))).scalars()
    }
    kept_success = {action for action, job in kept_jobs.items() if job.status == "succeeded" and job.result_json}
    rows = (
        (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id.in_(target_ids)))).scalars().all()
    )
    cloned = False
    for job in rows:
        if job.stage == "merged":
            continue
        if job.reference_hash and kept.reference_hash and job.reference_hash != kept.reference_hash:
            continue
        if _can_resume_job(job):
            continue
        if job.status == "succeeded" and job.result_json:
            if job.action in kept_success:
                continue
        elif job.action in kept_jobs:
            continue
        clone = _copy_job_to_pack(job, kept)
        db.add(clone)
        kept_jobs[job.action] = clone
        if job.status == "succeeded" and job.result_json:
            kept_success.add(job.action)
        cloned = True
    if cloned:
        # autoflush=False：先 flush 让迁入行进入资产引用查询，避免源素材被回收。
        await db.flush()


async def _retire_superseded_locked(db: AsyncSession, kept: CompanionVideoPack) -> set[str]:
    """kept 激活后删除同外观其余历史包（含任务行），返回可回收路径（调用方持有用户锁）。
    构建中的包不动；同血缘包上不可续跑的失败记录先迁到 kept。
    仍有可续跑任务的包保留，作为续跑入口。完整版本不堆积。"""
    targets = [
        pack
        for pack in (
            await db.execute(
                select(CompanionVideoPack).where(
                    CompanionVideoPack.user_id == kept.user_id,
                    CompanionVideoPack.outfit_id == kept.outfit_id,
                ),
            )
        ).scalars()
        if pack.pack_version < kept.pack_version and pack.status != "processing"
    ]
    succeeded = {
        job.action
        for job in (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == kept.id))).scalars()
        if job.status == "succeeded" and job.result_json
    }
    deletable: list[CompanionVideoPack] = []
    for pack in targets:
        jobs = (await db.execute(select(CompanionVideoJob).where(CompanionVideoJob.pack_id == pack.id))).scalars().all()
        if _same_generation_lineage(kept, pack) and any(
            _can_resume_job(job) and job.action not in succeeded for job in jobs
        ):
            continue
        deletable.append(pack)
    if not deletable:
        return set()
    await _carry_incomplete_jobs(db, kept, deletable)
    return await _remove_packs(db, kept.user_id, deletable)


def _unlink_assets(paths: set[str]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            unlink_companion_asset(path)


async def delete_pack(db: AsyncSession, user_id: int, pack_id: int) -> None:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError("找不到视频包")
        if pack.active or pack.status == "processing":
            raise VideoPackStateError("使用中或构建中的视频包不能删除")
        candidates = await _remove_packs(db, user_id, [pack])
        await db.commit()
    _unlink_assets(candidates)


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
        "identity_review": pack.identity_review,
        "identity_review_reason": pack.identity_review_reason,
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
        pack_context = _load_generation_context(pack)
        must_actions = pack_context.must_actions if pack_context is not None else []
        response["can_regenerate"] = bool(pack.reference_path) and pack.status in ("ready", "failed")
        response["can_retry"] = (
            bool(pack.reference_path)
            and pack.status == "failed"
            and pack_context is not None
            and pack_context.reference_alignment != "running"
            and (any(_can_resume_job(job) for job in pack_jobs) or _can_publish_jobs(pack_jobs, must_actions))
        )
        response["actions"] = []
        for job in sorted(pack_jobs, key=lambda job: _action_order(job.action)):
            script = ActionScriptEntry.model_validate_json(job.script_json) if job.script_json else None
            response["actions"].append(
                {
                    "action": job.action,
                    "name": job.name or "",
                    "kind": job.kind,
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
