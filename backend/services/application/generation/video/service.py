"""动作素材包编排：外观版本 → 动作片段处理 → 不可变包发布 → 激活；架构与入口见 generation/README.md。"""

import asyncio
import contextlib
import json
import tempfile
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from pathlib import Path

from components import (
    SESSION_LOCAL,
    SETTINGS,
    TaskBag,
    get_logger,
    is_user_in_maintenance,
    safe_json_loads,
    track_user_task,
)
from modules.companion import (
    ABSOLUTE_MAX_DURATION_SECONDS,
    REQUIRED_SYSTEM_SLOTS,
    SYSTEM_ACTION_MEDIA_TYPES,
    SYSTEM_SLOTS,
    ActionResult,
    AvatarAsset,
    CompanionAction,
    CompanionActionPack,
    CompanionOutfit,
    ImageClipSpec,
    MediaReviewPublication,
    OutfitSource,
    PeekGeometry,
    RemovedActionVideoTask,
    VideoActionImageResponse,
    VideoActionVideoResponse,
    VideoClipSpec,
    VideoPackResponse,
    make_action_reference_hash,
    parse_content_rect,
    removed_video_tasks,
)
from modules.ws import emit_ws_event
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from services.domains.actions import (
    accept_action_asset,
    accepted_action_asset,
    action_asset_paths,
    clear_action_attempt,
    emit_catalog_changed,
    fulfill_deferred_play_intents,
    publish_action_catalog,
    retire_action_assets,
)
from services.domains.assets import ACTION_ASSET_FIELDS
from services.domains.companion import (
    character_snapshot_is_current,
    get_or_create_persona,
    load_persona_definition,
    render_character_profile,
    require_character_snapshot,
)
from services.infrastructure.assets import (
    action_pose_asset_path,
    action_source_asset_path,
    build_data_uri,
    download_media_result,
    image_mime_for_extension,
    pack_asset_directory,
    parse_companion_asset_path,
    resolve_companion_asset_path,
    save_action_pose_asset_async,
    save_action_source_asset_async,
    save_companion_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
    unlink_companion_asset,
)
from services.infrastructure.llm import (
    LlmCallBlockedError,
    ProviderError,
    ProviderResultUnknownError,
    VideoGenProvider,
    VideoGenRequest,
    VideoJobStatus,
    VisualReasoningError,
    build_provider,
    execute_with_fallback,
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
    ActionMaterialRejectedError,
    VideoProcessError,
    action_frame_video_input,
    build_hitmask,
    extract_cover,
    matte_video,
    prepare_action_clip,
    prepare_action_frame,
    prepare_action_image,
    require_matting_model,
    sample_key_frames,
    validate_action_clip,
)

from ..avatar_service import FULLBODY_SIZE, get_avatar_job_lock, load_avatar_bytes_as_data_uri, read_portrait_bytes
from ..character_images import ImageChainState, generate_character_images
from ..identity_review import review_character_frames, score_character_frames
from ..image_generation import ImageGenerationError, resolve_image_gen_chain
from ..media_chain import (
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    MediaProviderFailedError,
    media_failure_reason,
    resolve_frozen_media_provider,
    spawn_removed_action_task_cancellation,
    video_failure_message,
    video_provider_failure_reason,
)
from ..media_review import create_media_review, reject_pending_action_reviews
from ..outfit_service import wait_outfit_description
from ..paid_work import GenerationWorkPaused, require_new_generation_call
from ..video_jobs import VideoPollTimeoutError, poll_video_task
from ..visual_identity import align_character_reference
from .script import (
    ActionScriptEntry,
    ActionSpec,
    ImageActionScriptEntry,
    ImageActionSpec,
    VideoActionScriptEntry,
    VideoActionSpec,
    VideoScriptError,
    build_action_image_prompt,
    build_pose_prompt,
    build_video_prompt,
    compose_action_image_descriptions,
    compose_action_script,
    inspect_peek_geometry,
    parse_action_script_entry,
)
from .state import GenerationContext

logger = get_logger(__name__)

# 上传导入的默认像素画布；生成包仅取其比例。
_DEFAULT_CANVAS = (512, 768)
_SYSTEM_ACTION_SECONDS = 2.0
_SOURCE_EXT_BY_MIME = {
    "image/png": ".png",
    "image/webp": ".webp",
    "video/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}
# 生成源接受的视频容器（供应商产物与内部中间产物）
_SOURCE_MEDIA_EXTS = {"mp4", "webm", "mov", "mkv"}

_TASKS = TaskBag("companion.video")
_GEN_INFLIGHT: set[int] = set()
_GEN_PENDING: set[int] = set()


async def _process_thread[T](fn: Callable[..., T], *args: object, **kwargs: object) -> T:
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


class VideoPackError(RuntimeError):
    """动作包流程错误；str 为公开文案。"""


class VideoPackNotFoundError(VideoPackError):
    """目标动作包不存在或不属于调用者。"""


class VideoPackStateError(VideoPackError):
    """状态守卫拒绝（未就绪 / 状态冲突 / 参考版本过期）。"""


class _GenerationRowGoneError(Exception):
    """生成中的动作行已删除，或包已离开生成流程；只跳过受影响的目标。"""


def _pack_dir(pack: CompanionActionPack) -> str:
    return pack_asset_directory(pack.outfit_id, pack.id)


async def _require_new_paid_step(pack: CompanionActionPack, context: GenerationContext) -> None:
    require_new_generation_call(pack.user_id)
    async with SESSION_LOCAL() as db:
        avatar = await _active_avatar(db, pack.user_id)
    try:
        await _require_current_identity_image(context, avatar)
    except VideoPackError as exc:
        raise LlmCallBlockedError(str(exc)) from exc
    require_new_generation_call(pack.user_id)


async def require_pack_generation_identity(db: AsyncSession, user_id: int, pack_id: int) -> None:
    pack = await _get_pack(db, user_id, pack_id)
    context = _load_generation_context(pack) if pack is not None else None
    if pack is None or context is None or not pack.reference_path:
        raise VideoPackStateError("该动作包没有可制作的冻结参考，请重新生成动作包")
    await _require_current_identity_image(context, await _active_avatar(db, user_id))


async def _active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    return (
        await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))
    ).scalar_one_or_none()


async def _active_outfit_id(db: AsyncSession, user_id: int) -> int | None:
    return (
        await db.execute(
            select(CompanionOutfit.id).where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True)),
        )
    ).scalar_one_or_none()


async def _reference_hash(outfit: CompanionOutfit | None, avatar: AvatarAsset | None) -> str:
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


async def _copy_pack_value(
    value: object,
    pack: CompanionActionPack,
    copied: dict[str, str],
) -> object:
    if isinstance(value, list):
        return [await _copy_pack_value(item, pack, copied) for item in value]
    if isinstance(value, dict):
        return {
            key: _pack_dir(pack) if key == "storage_directory" else await _copy_pack_value(item, pack, copied)
            for key, item in value.items()
        }
    if not isinstance(value, str):
        return value
    parsed = parse_companion_asset_path(value)
    if parsed is not None:
        if parsed[0] != pack.user_id:
            raise VideoPackStateError("继承素材不属于当前账户")
        if value not in copied:
            directory = _pack_dir(pack)
            resolved = resolve_companion_asset_path(*parsed)
            if resolved is None:
                # 进度会预登记尚未落盘的源素材；恢复时仍使用目标包的确定性路径。
                return f"companion-assets/{pack.user_id}/{directory}/{parsed[1].rsplit('/', 1)[-1]}"
            else:
                copied[value] = await save_companion_asset_async(
                    await _process_thread(resolved[0].read_bytes),
                    user_id=pack.user_id,
                    directory=directory,
                    label=resolved[0].stem,
                    ext=resolved[0].suffix.lstrip("."),
                )
        return copied[value]
    if value.startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
        return json.dumps(await _copy_pack_value(decoded, pack, copied), ensure_ascii=False)
    return value


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
    """交付片段的首、中、末帧 data URI，供身份评分、复核与探身定位。"""
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


def require_action_matting_model() -> None:
    """动作素材都要经过抠像；模型缺失时在任何付费生成前拒绝。"""
    try:
        require_matting_model()
    except VideoProcessError as exc:
        raise VideoPackStateError(str(exc)) from exc


async def _reject_concurrent_build(db: AsyncSession, user_id: int) -> None:
    """同一用户同时只允许一个构建中的动作包，避免发布与激活竞态。"""
    processing = (
        await db.execute(
            select(CompanionActionPack.id).where(
                CompanionActionPack.user_id == user_id,
                CompanionActionPack.status == "processing",
            ),
        )
    ).scalar_one_or_none()
    if processing is not None:
        raise VideoPackStateError("已有动作包任务进行中，请等待完成后再试")


async def create_pack_from_clips(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int] = _DEFAULT_CANVAS,
    action_ranges: dict[str, tuple[float, float]] | None = None,
) -> CompanionActionPack:
    """上传动作素材创建动作包；处理在后台进行。clips 为「动作键 → (源字节, MIME)」，action_ranges 提供长视频起止秒。失败只影响本次包。"""
    if not set(REQUIRED_SYSTEM_SLOTS) <= set(clips) or not set(clips) <= set(SYSTEM_SLOTS):
        raise VideoPackError("片段须包含待机与拖拽，且只能是已知动作")
    canvas_w, canvas_h = canvas
    if not (0 < canvas_w <= MAX_CANVAS_WIDTH and 0 < canvas_h <= MAX_CANVAS_HEIGHT) or canvas_w % 2 or canvas_h % 2:
        raise VideoPackError("画布尺寸超出限制")

    async with get_avatar_job_lock(user_id):
        persona = await get_or_create_persona(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成角色设定再创建动作包")
        await _reject_concurrent_build(db, user_id)
        outfit = (
            await db.execute(
                select(CompanionOutfit).where(CompanionOutfit.id == outfit_id, CompanionOutfit.user_id == user_id),
            )
        ).scalar_one_or_none()
        if outfit is None:
            raise VideoPackError("找不到对应的外观")
        if outfit.status != "ready":
            raise VideoPackStateError("外观尚未确认，无法创建动作包")
        avatar = await _active_avatar(db, user_id)
        reference_hash = await _reference_hash(outfit, avatar)

        for action, (data, _content_type) in clips.items():
            if len(data) > MAX_SOURCE_BYTES:
                raise VideoPackError("动作片段超过大小上限")

        pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
        pack.canvas_spec = json.dumps({"width": canvas_w, "height": canvas_h})
        for action, (_data, content_type) in clips.items():
            media_type = "image" if content_type.startswith("image/") else "video"
            db.add(
                CompanionAction(
                    user_id=user_id,
                    pack_id=pack.id,
                    outfit_id=outfit.id,
                    key=action,
                    system_slot=action,
                    media_type=media_type,
                    kind="loop" if media_type == "video" else None,
                    status="queued",
                    stage="process",
                    reference_hash=reference_hash,
                ),
            )
        await db.commit()
        await db.refresh(pack)

    _kick_build(
        pack.id,
        user_id,
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
) -> CompanionActionPack:
    """按参考生成：单个系统动作在 ready 包原地重做（保 pack_id 只推进素材版本），包未 ready 时才形成新版本并复用冻结参考。"""
    if (source_pack_id is None) != (action is None) or (action is not None and action not in SYSTEM_SLOTS):
        raise VideoPackStateError("单动作请求必须指定有效动作包与动作")
    async with get_avatar_job_lock(user_id), contextlib.AsyncExitStack() as pending_assets:
        persona = await get_or_create_persona(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成角色设定")
        source = await _get_pack(db, user_id, source_pack_id) if source_pack_id is not None else None
        if source_pack_id is not None and (
            source is None or source.status not in ("ready", "failed") or not source.reference_path
        ):
            raise VideoPackStateError("该动作包不能生成该动作")
        # 同一外观快照：ready 包上单动作原地重做，不新建 pack。
        if source is not None and action is not None and source.status == "ready":
            await _reject_concurrent_build(db, user_id)
            require_action_matting_model()
            source_context = _load_generation_context(source)
            if source_context is None:
                raise VideoPackStateError("该动作包的制作资料不完整，请重新生成动作包")
            await _require_current_identity_image(source_context, await _active_avatar(db, user_id))
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
                select(CompanionActionPack)
                .where(
                    CompanionActionPack.user_id == user_id,
                    CompanionActionPack.outfit_id == outfit.id,
                )
                .order_by(CompanionActionPack.id.desc())
                .limit(1),
            )
            if existing is not None:
                return existing
            if outfit.initial_video_started:
                raise VideoPackStateError("默认动作包任务已移除，请在外观页重新生成")
        await _reject_concurrent_build(db, user_id)
        avatar = await _active_avatar(db, user_id)
        if source is not None and (avatar is None or source.avatar_id != avatar.id):
            raise VideoPackStateError("角色形象已切换，请重新生成动作包")
        reference_hash = source.reference_hash if source is not None else await _reference_hash(outfit, avatar)
        source_context = _load_generation_context(source) if source is not None else None
        if source is not None and source_context is None:
            raise VideoPackStateError("该动作包的制作资料不完整，请重新生成动作包")
        if source_context is not None:
            await _require_current_identity_image(source_context, avatar)
        identity = (
            source_context.identity if source_context is not None else await require_character_snapshot(db, user_id)
        )
        if source_context is not None and source_context.reference_alignment != "ready":
            raise VideoPackStateError("参考图尚未准备完成，请重新生成动作包")
        if not force and source is None:
            reusable = (
                await db.execute(
                    select(CompanionActionPack)
                    .where(
                        CompanionActionPack.user_id == user_id,
                        CompanionActionPack.outfit_id == outfit.id,
                        CompanionActionPack.reference_hash == reference_hash,
                        CompanionActionPack.status == "ready",
                        CompanionActionPack.reference_path != "",
                    )
                    .order_by(CompanionActionPack.pack_version.desc())
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
                _, removed = await _retire_superseded_locked(db, reusable)
                await db.commit()
                spawn_removed_action_task_cancellation(user_id, removed)

                return reusable
        system_seconds: float | None = None
        system_providers: list[FrozenMediaProvider] = []
        if not await resolve_vision_chain(db, user_id):
            raise VideoPackStateError("未配置视觉模型，无法根据角色参考图撰写动作脚本")
        image_chain, image_error = await resolve_image_gen_chain(db, user_id, has_reference=True, image_edit=True)
        if not image_chain:
            raise VideoPackStateError(image_error or "请配置图像编辑供应商以生成动作姿态")
        require_action_matting_model()
        pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
        directory = _pack_dir(pack)
        previous: dict[str, CompanionAction] = {}
        if source is not None:
            previous = {
                job.key: job
                for job in (
                    await db.execute(select(CompanionAction).where(CompanionAction.pack_id == source.id))
                ).scalars()
            }
            # 必需动作结果未知且不是本次目标时拒绝，避免目标动作付费完成后整包仍无法发布。
            for key in REQUIRED_SYSTEM_SLOTS:
                if key == action:
                    continue
                old = _inheritable_job(previous.get(key))
                if old is not None and accepted_action_asset(old) is None and not _can_resume_job(old):
                    raise VideoPackStateError("请先重做失败的必需动作，再补齐其他动作")
            copied_reference = await _copy_portrait_asset(
                source.reference_path,
                user_id=user_id,
                label="video_reference",
                directory=directory,
            )
            if copied_reference is None:
                raise VideoPackStateError("该动作包的参考图无法读取")
            reference_path = copied_reference
            pending_assets.callback(unlink_companion_asset, reference_path)
        else:
            copied = await _copy_portrait_asset(
                outfit.fullbody_url,
                user_id=user_id,
                label="video_reference",
                directory=directory,
            )
            if copied is None:
                raise VideoPackStateError("外观参考图不可读")
            reference_path = copied
            pending_assets.callback(unlink_companion_asset, reference_path)
        active_outfit_id = await _active_outfit_id(db, user_id)
        if source_context is not None:
            action_feedback = dict(source_context.action_feedback)
            if action is not None:
                action_feedback[action] = feedback.strip()[:1000]
            copied: dict[str, str] = {source.reference_path: reference_path}
            try:
                context = GenerationContext.model_validate(
                    await _copy_pack_value(source_context.model_dump(), pack, copied),
                )
            finally:
                for path in copied.values():
                    pending_assets.callback(unlink_companion_asset, path)
            context.action_feedback = action_feedback
            context.active_outfit_id = active_outfit_id
        else:
            seed_path = avatar.seed_fullbody_url if avatar else ""
            identity_reference_path = await _copy_portrait_asset(
                seed_path,
                user_id=user_id,
                label="video_identity",
                directory=directory,
            )
            if identity_reference_path is None:
                raise VideoPackStateError("全身形象无法读取")
            pending_assets.callback(unlink_companion_asset, identity_reference_path)
            applied_identity_path = OutfitSource.load(outfit.source_json).identity_reference_path
            context = GenerationContext(
                identity=identity,
                identity_reference_path=identity_reference_path,
                # 外观按制作时的全身身份图生成；身份图已更换时先校准冻结参考。
                reference_alignment="ready" if applied_identity_path == seed_path else "pending",
                persona_definition=load_persona_definition(persona),
                personality_tags=safe_json_loads(persona.personality_tags_json or "[]", default=[]),
                outfit_description=outfit.description or "",
                outfit_description_frozen=bool(outfit.description),
                feedback=feedback,
                active_outfit_id=outfit.id if initial_only else active_outfit_id,
                must_actions=[],
            )
        context.storage_directory = directory
        context.reference_chain.storage_directory = directory
        pack.reference_path = reference_path
        canvas_w, canvas_h = _DEFAULT_CANVAS
        pack.canvas_spec = json.dumps({"width": canvas_w, "height": canvas_h}, ensure_ascii=False)
        # 冻结评审用快照：角色卡资料与当前着装描述（独立评审判断可行性依据）。
        pack.character_snapshot = json.dumps(
            {"profile": render_character_profile(identity)},
            ensure_ascii=False,
        )
        pack.outfit_snapshot = json.dumps({"description": context.outfit_description}, ensure_ascii=False)
        # 新整包只建必需动作；单动作继承源包动作并补齐/重做目标。成功直接复用，可续跑失败重新排队，未知失败不阻塞。must_actions 为本版本必须成功的集合。
        if source is not None:
            action_keys = set(previous) | {action, *REQUIRED_SYSTEM_SLOTS}
        else:
            action_keys = set(REQUIRED_SYSTEM_SLOTS)
        must_actions: set[str] = set(REQUIRED_SYSTEM_SLOTS)
        for key in sorted(action_keys, key=_action_order):
            old = _inheritable_job(previous.get(key)) if key != action else None
            if old is None:
                media_type = SYSTEM_ACTION_MEDIA_TYPES.get(key, "video")
                if media_type == "video" and not system_providers:
                    system_seconds, system_providers = await _resolve_action_video_plan(
                        user_id,
                        needs_loop_frames=True,
                        duration_seconds=_SYSTEM_ACTION_SECONDS,
                    )
                job = CompanionAction(
                    user_id=user_id,
                    pack_id=pack.id,
                    outfit_id=outfit.id,
                    key=key,
                    system_slot=key if key in SYSTEM_SLOTS else "",
                    media_type=media_type,
                    kind=("loop" if key in SYSTEM_SLOTS else "once") if media_type == "video" else None,
                    target_duration_seconds=system_seconds if media_type == "video" else None,
                    status="queued",
                    stage="script",
                    reference_hash=reference_hash,
                )
                if media_type == "video" and system_seconds is not None:
                    _freeze_action_video_plan(job, system_seconds, system_providers)
                must_actions.add(key)
            else:
                job = await _copy_job_to_pack(old, pack)
                job.outfit_id, job.reference_hash = outfit.id, reference_hash
                if accepted_action_asset(job) is not None:
                    must_actions.add(key)
                elif _can_resume_job(old):
                    job.status, job.error = "queued", None
                    must_actions.add(key)
            db.add(job)
        context = context.model_copy(update={"must_actions": sorted(must_actions, key=_action_order)})
        pack.context_json = context.model_dump_json()
        await db.commit()
        pending_assets.pop_all()
        await db.refresh(pack)
    _kick_generate(pack.id, user_id)
    return pack


def _action_order(action: str) -> tuple[int, str]:
    """任务排序键：已知动作按全集聚合顺序，可选动作排在其后并按动作名稳定排序。"""
    return (SYSTEM_SLOTS.index(action) if action in SYSTEM_SLOTS else len(SYSTEM_SLOTS), action)


def _can_resume_job(job: CompanionAction) -> bool:
    if job.status in ("succeeded", "review") or job.stage in ("merged", "invalid_asset"):
        return False
    if job.generation_state_json:
        state = MediaChainState.model_validate_json(job.generation_state_json)
        if (state.phase == "submitting" or state.stop_reason == "result_unknown") and state.best() is None:
            return False
        if state.phase == "ready" and not state.needs_next() and state.best() is None:
            return False
    if job.pose_generation_state_json:
        pose = ImageChainState.model_validate_json(job.pose_generation_state_json)
        if (pose.phase == "submitting" or pose.stop_reason == "result_unknown") and pose.best() is None:
            return False
    return bool(
        job.provider_task_id
        or job.artifact_path
        or job.generation_state_json
        or job.pose_generation_state_json
        or job.stage == "script"
        or (job.stage == "pose" and job.pose_path),
    )


def _inheritable_job(job: CompanionAction | None) -> CompanionAction | None:
    """已迁出续跑入口的任务行不参与继承与再清理。"""
    if job is None or job.stage == "merged":
        return None
    return job


def _must_succeed_actions(context_must: list[str]) -> set[str]:
    return set(context_must) | set(REQUIRED_SYSTEM_SLOTS)


def _can_publish_jobs(jobs: Sequence[CompanionAction], context_must: list[str]) -> bool:
    by_action = {job.key: job for job in jobs}
    for action in _must_succeed_actions(context_must):
        job = by_action.get(action)
        if job is None or accepted_action_asset(job) is None:
            return False
    return True


async def retry_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionActionPack:
    """只恢复已知任务或本地素材；不自动重发结果未知的生成请求。"""
    async with get_avatar_job_lock(user_id):
        await _reject_concurrent_build(db, user_id)
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None or pack.status != "failed" or not pack.reference_path:
            raise VideoPackStateError("该动作包没有可继续的任务")
        outfit = await db.get(CompanionOutfit, pack.outfit_id)
        avatar = await _active_avatar(db, user_id)
        if outfit is None or avatar is None or pack.avatar_id != avatar.id:
            raise VideoPackStateError("该动作包对应的角色形象已切换，请重新生成动作包")
        if not _artifact_abs_path(pack.reference_path).is_file():
            raise VideoPackStateError("该动作包的参考图无法读取")
        context = _load_generation_context(pack)
        if context is None:
            raise VideoPackStateError("该动作包的制作资料不完整，请重新生成动作包")
        if context.reference_chain.phase == "submitting" and context.reference_chain.best() is None:
            raise VideoPackStateError("参考图生成结果未知，请核对后重新生成动作包")
        jobs = list(
            (await db.execute(select(CompanionAction).where(CompanionAction.pack_id == pack_id))).scalars(),
        )
        newer = await _newer_ready_pack(db, pack)
        if newer is not None and not _same_generation_lineage(pack, newer):
            newer = None
        if newer is not None:
            by_action = {job.key: job for job in jobs}
            newer_jobs = (
                (await db.execute(select(CompanionAction).where(CompanionAction.pack_id == newer.id))).scalars().all()
            )
            for job in newer_jobs:
                # 最新成功结果优先；尚未完成的动作沿用原任务句柄和脚本。
                if accepted_action_asset(job) is not None or job.key not in by_action:
                    by_action[job.key] = job
            jobs = sorted(by_action.values(), key=lambda job: _action_order(job.key))
            must_actions = _must_succeed_actions(context.must_actions)
            must_actions.update(
                job.key for job in jobs if accepted_action_asset(job) is not None or _can_resume_job(job)
            )
            context = context.model_copy(update={"must_actions": sorted(must_actions, key=_action_order)})
        recoverable = [job for job in jobs if _can_resume_job(job)]
        if not recoverable and not _can_publish_jobs(jobs, context.must_actions):
            raise VideoPackStateError("提交结果未知，请核对供应商任务后选择重做动作")
        if recoverable:
            require_action_matting_model()
        if newer is not None:
            # 旧包不能原地变成更旧的激活版本；合并结果走新不可变版本，沿用原包冻结参考与快照。
            origin = pack
            pack = await _insert_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=origin.reference_hash)
            pack.reference_path = origin.reference_path
            pack.canvas_spec = origin.canvas_spec
            pack.character_snapshot = origin.character_snapshot
            pack.outfit_snapshot = origin.outfit_snapshot
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


async def _newer_ready_pack(db: AsyncSession, pack: CompanionActionPack) -> CompanionActionPack | None:
    return await db.scalar(
        select(CompanionActionPack)
        .where(
            CompanionActionPack.user_id == pack.user_id,
            CompanionActionPack.outfit_id == pack.outfit_id,
            CompanionActionPack.pack_version > pack.pack_version,
            CompanionActionPack.status == "ready",
        )
        .order_by(CompanionActionPack.pack_version.desc())
        .limit(1),
    )


async def _insert_pack(
    db: AsyncSession,
    user_id: int,
    *,
    avatar: AvatarAsset | None,
    outfit: CompanionOutfit,
    reference_hash: str,
) -> CompanionActionPack:
    if outfit.is_initial:
        outfit.initial_video_started = True
        outfit.initial_video_error = None
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.outfit.updated",
            payload={"outfit_id": outfit.id, "worn": False},
        )
    pack = CompanionActionPack(
        user_id=user_id,
        avatar_id=avatar.id if avatar is not None else None,
        outfit_id=outfit.id,
        status="processing",
        reference_hash=reference_hash,
    )
    # 版本号 = 同外观历史最大版本 + 1；版本不可覆盖，单动作请求即新版本。
    latest_version = (
        await db.execute(
            select(CompanionActionPack.pack_version)
            .where(CompanionActionPack.user_id == user_id, CompanionActionPack.outfit_id == outfit.id)
            .order_by(CompanionActionPack.pack_version.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    pack.pack_version = (latest_version or 0) + 1
    db.add(pack)
    await db.flush()
    return pack


def _kick_build(
    pack_id: int,
    user_id: int,
    *,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int],
    action_ranges: dict[str, tuple[float, float]],
) -> None:
    task = asyncio.create_task(
        _build_pack(pack_id, clips=clips, canvas=canvas, action_ranges=action_ranges),
        name=f"companion.video.build.{pack_id}",
    )
    _TASKS.add(task)
    # 导入不涉及付费调用：用户维护时直接取消，中断的包按失败落库。
    track_user_task(user_id, task)


async def _prepare_clip(
    work: Path,
    action: str,
    src: Path,
    user_id: int,
    canvas_w: int,
    canvas_h: int,
    *,
    start: float | None = None,
    end: float | None = None,
    preserve_resolution: bool = False,
    directory: str,
) -> ActionResult:
    """处理单动作片段并转存片段、封面与命中遮罩；封面/遮罩从最终交付片段读取（libvpx 解码 VP9 alpha），遮罩落盘供 hitmask_ref 按帧查询。"""
    dst = work / f"{action}.{TARGET_EXT}"
    processed = await _process_thread(
        prepare_action_clip,
        src,
        dst,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        start_seconds=start,
        end_seconds=end,
        preserve_resolution=preserve_resolution,
    )
    canvas_w, canvas_h = processed.width, processed.height
    hitmask = await _process_thread(build_hitmask, dst, canvas_w=canvas_w, canvas_h=canvas_h)
    cover = await _process_thread(extract_cover, dst, canvas_w=canvas_w, canvas_h=canvas_h)
    stored = await save_companion_asset_async(
        await _process_thread(dst.read_bytes),
        user_id=user_id,
        directory=directory,
        label=f"video_{action}",
        ext="webm",
    )
    saved = [stored]
    try:
        cover_stored = await save_companion_asset_async(
            cover,
            user_id=user_id,
            directory=directory,
            label=f"video_{action}_cover",
            ext="webp",
        )
        saved.append(cover_stored)
        hitmask_stored = await save_companion_asset_async(
            json.dumps(hitmask).encode("utf-8"),
            user_id=user_id,
            directory=directory,
            label=f"video_{action}_hitmask",
            ext="json",
        )
    except BaseException:
        for path in saved:
            unlink_companion_asset(path)
        raise
    return ActionResult(
        clip=VideoClipSpec(
            action=action,
            path=stored,
            sha256=processed.sha256,
            frames=processed.frames,
            duration_ms=processed.duration_ms,
            width=processed.width,
            height=processed.height,
            content_rect=_hitmask_content_rect(hitmask),
        ),
        cover_path=cover_stored,
        hitmask_path=hitmask_stored,
    )


async def _prepare_image(
    work: Path,
    action: str,
    src: Path,
    user_id: int,
    canvas_w: int,
    canvas_h: int,
    *,
    directory: str,
) -> ActionResult:
    dst = work / f"{action}.png"
    processed = await _process_thread(prepare_action_image, src, dst, canvas_w=canvas_w, canvas_h=canvas_h)
    saved: list[str] = []
    try:
        stored = await save_companion_asset_async(
            await _process_thread(dst.read_bytes),
            user_id=user_id,
            directory=directory,
            label=f"action_image_{action}",
            ext="png",
        )
        saved.append(stored)
        cover = await save_companion_asset_async(
            processed.cover,
            user_id=user_id,
            directory=directory,
            label=f"action_image_{action}_cover",
            ext="webp",
        )
        saved.append(cover)
        hitmask = await save_companion_asset_async(
            json.dumps(processed.hitmask).encode(),
            user_id=user_id,
            directory=directory,
            label=f"action_image_{action}_hitmask",
            ext="json",
        )
        saved.append(hitmask)
    except BaseException:
        for path in saved:
            unlink_companion_asset(path)
        raise
    return ActionResult(
        clip=ImageClipSpec(
            action=action,
            path=stored,
            sha256=processed.sha256,
            width=processed.width,
            height=processed.height,
            content_rect=processed.content_rect,
        ),
        cover_path=cover,
        hitmask_path=hitmask,
    )


async def _emit_pack_event(user_id: int, event_type: str, payload: dict[str, object]) -> None:
    """独立短会话写入 WS 事件并提交，进度与结果在后台任务各阶段可发出。"""
    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
        await db.commit()


async def _advance_job(job_id: int, *, stage: str, status: str = "processing", **fields: object) -> None:
    """推进生成任务行阶段；终态行不再被覆写，行已删除时中止该动作。"""
    async with SESSION_LOCAL() as db:
        job = await db.get(CompanionAction, job_id)
        if job is None:
            raise _GenerationRowGoneError
        if job.status in ("succeeded", "review", "failed"):
            return
        previous = accepted_action_asset(job)
        job.status = status
        job.stage = stage
        for key, value in fields.items():
            setattr(job, key, value)
        if status == "succeeded":
            await retire_action_assets(db, job.user_id, previous.paths() if previous else [])
            accept_action_asset(job)
        await db.commit()


async def _save_action_state(
    job: CompanionAction,
    state: MediaChainState,
    *,
    stage: str | None = None,
    status: str = "processing",
    **fields: object,
) -> None:
    """持久化素材链进度；stage 缺省时沿用任务当前阶段。"""
    job.generation_state_json = state.model_dump_json()
    if stage is not None:
        job.stage = stage
    await _advance_job(
        job.id,
        stage=job.stage,
        status=status,
        generation_state_json=job.generation_state_json,
        **fields,
    )


async def _mark_pack_failed(
    db: AsyncSession,
    pack: CompanionActionPack,
    message: str,
    *,
    stage: str | None = None,
) -> None:
    """包与未终态任务置失败并写失败事件（调用方提交）；message 为公开文案，同时作为任务与事件原因。"""
    pack.status = "failed"
    pack.error = message
    values: dict[str, object] = {"status": "failed", "error": message}
    if stage is not None:
        values["stage"] = stage
    await db.execute(
        update(CompanionAction)
        .where(
            CompanionAction.pack_id == pack.id,
            CompanionAction.status.not_in(("succeeded", "review", "failed", "result_unknown")),
        )
        .values(**values)
        .execution_options(synchronize_session=False),
    )
    emit_ws_event(
        db,
        user_id=pack.user_id,
        event_type="companion.video.failed",
        payload={"packId": pack.id, "outfitId": pack.outfit_id, "reason": message[:300]},
    )


async def _fail_pack(pack_id: int, message: str) -> None:
    """构建中的包置失败；message 为公开文案。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None or pack.status != "processing":
            return
        await _mark_pack_failed(db, pack, message[:500])
        await db.commit()


async def _review_pack_identity(
    pack_id: int,
    user_id: int,
    context: GenerationContext,
    results: dict[str, ActionResult],
    *,
    before_submit: Callable[[], Awaitable[None]],
) -> tuple[str, str]:
    """必需动作的静态图或视频采样帧与冻结身份图复核；不可用时转人工预览。"""
    try:
        reference_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, context.identity_reference_path)
        frame_uris: list[str] = []
        for slot in REQUIRED_SYSTEM_SLOTS:
            if slot in results:
                frame_uris.extend(await _action_sample_uris(results[slot].clip))
        return await review_character_frames(
            user_id,
            reference_uri,
            tuple(frame_uris),
            pack_wide=True,
            identity_text=render_character_profile(context.identity),
            before_submit=before_submit,
        )
    except LlmCallBlockedError:
        raise
    except Exception:
        logger.warning("video pack identity review failed", extra={"pack_id": pack_id}, exc_info=True)
        return "review", "动作包的自动检查未完成，请预览后手动启用"


async def _publish_ready(pack_id: int, results: dict[str, ActionResult]) -> None:
    """发布不可变资产；生成包先做整包身份复核，迟到结果保留为历史版本。results 为已成功动作（封面取 idle）；自动激活核对参考与角色卡，与 ready 发布同事务。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None:
            return
        user_id = pack.user_id
        context = _load_generation_context(pack)
    idle = results.get("idle")
    cover_path = idle.cover_path if idle is not None else None
    if context is None:
        review_status, review_reason = "accepted", ""
    elif cover_path:
        review_status, review_reason = await _review_pack_identity(
            pack_id,
            user_id,
            context,
            results,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
    else:
        review_status, review_reason = "review", "动作封面不可用，请预览后手动启用"
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None or pack.status != "processing":
            return
        outfit = await db.get(CompanionOutfit, pack.outfit_id) if pack.outfit_id is not None else None
        avatar = await _active_avatar(db, pack.user_id)
        reference_is_current = outfit is not None and await _reference_hash(outfit, avatar) == pack.reference_hash
        if not reference_is_current and not pack.reference_path:
            await _mark_pack_failed(db, pack, "外观参考已变更，本次生成结果已过期，请重新生成", stage="publish")
            await db.commit()
            return
        pack.status = "ready"
        pack.error = None
        pack.identity_review = review_status
        pack.identity_review_reason = review_reason
        if cover_path:
            pack.cover_path = cover_path
        # 动作目录 = 当前动作包可用清单：从任务行聚合发布；发布失败时不广播目录也不自动启用，保留当前激活包。
        published = await _publish_catalog(db, pack) is not None
        emit_ws_event(
            db,
            user_id=pack.user_id,
            event_type="companion.video.ready",
            payload={"packId": pack.id, "outfitId": pack.outfit_id, "packVersion": pack.pack_version},
        )
        if published:
            emit_catalog_changed(db, pack)
        removed_tasks: list[RemovedActionVideoTask] = []
        if (
            published
            and context is not None
            and review_status in ("pass", "accepted")
            and reference_is_current
            and await _newer_ready_pack(db, pack) is None
            and await _active_outfit_id(db, pack.user_id) in (context.active_outfit_id, pack.outfit_id)
            and await character_snapshot_is_current(db, pack.user_id, context.identity)
        ):
            await _activate_locked(db, pack)
            _, removed_tasks = await _retire_superseded_locked(db, pack)
        await db.commit()
        spawn_removed_action_task_cancellation(pack.user_id, removed_tasks)


async def _publish_catalog(db: AsyncSession, pack: CompanionActionPack) -> int | None:
    """从任务行聚合发布动作目录（spiritagent.action.pack），CAS 推进。必需槽位不齐或失败返回 None：不回滚已成功素材，可再次发布恢复。"""
    try:
        return await publish_action_catalog(db, pack)
    except Exception:  # noqa: BLE001 — 目录发布失败不回滚素材；调用方据 None 跳过目录广播与自动启用
        logger.exception("action catalog publish failed", extra={"pack_id": pack.id})
        return None


async def _build_pack(
    pack_id: int,
    *,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int],
    action_ranges: dict[str, tuple[float, float]],
) -> None:
    """后台构建（上传导入）：逐动作处理并落库 → 发布。"""
    canvas_w, canvas_h = canvas
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None:
                return
            user_id = pack.user_id

        results: dict[str, ActionResult] = {}
        with tempfile.TemporaryDirectory(prefix="video-pack-") as tmp:
            work = Path(tmp)
            for action, (data, content_type) in clips.items():
                src = work / f"{action}_src{_source_ext(content_type)}"
                await _process_thread(src.write_bytes, data)
                start, end = action_ranges.get(action, (None, None))
                if content_type.startswith("image/"):
                    result = await _prepare_image(
                        work,
                        action,
                        src,
                        user_id,
                        canvas_w,
                        canvas_h,
                        directory=_pack_dir(pack),
                    )
                else:
                    result = await _prepare_clip(
                        work,
                        action,
                        src,
                        user_id,
                        canvas_w,
                        canvas_h,
                        start=start,
                        end=end,
                        directory=_pack_dir(pack),
                    )
                results[action] = result
                async with SESSION_LOCAL() as db:
                    await db.execute(
                        update(CompanionAction)
                        .where(CompanionAction.pack_id == pack_id, CompanionAction.key == action)
                        .values(
                            status="succeeded",
                            stage="publish",
                            loopable=True if result.clip.media_type == "video" else None,
                            **_clip_row_values(result),
                        ),
                    )
                    await db.commit()

        await _publish_ready(pack_id, results)
    except asyncio.CancelledError:
        # 用户维护或停机中断导入：上传源片段不可复用，与重启恢复一致按失败落库，避免包一直占着构建位。
        await _fail_pack(pack_id, "处理已中断，请重新提交")
        raise
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底：失败必须落库可见
        logger.exception("video pack build failed", extra={"pack_id": pack_id})
        # 只有处理错误自带公开文案；其余异常（含路径等内部信息）只进日志。
        public = str(exc) if isinstance(exc, (VideoProcessError, VideoPackError)) else "动作片段处理失败，请重新提交"
        await _fail_pack(pack_id, public)


def _action_resolution(
    provider: VideoGenProvider,
    *,
    needs_loop_frames: bool,
    duration_seconds: float,
) -> str | None:
    if not provider.supports_first_frame:
        return None
    if needs_loop_frames and not provider.supports_loop_frames:
        return None
    seconds = int(round(duration_seconds))
    if provider.durations is not None and seconds not in provider.durations:
        return None
    return provider.max_resolution(
        duration=seconds,
        first_frame=True,
        last_frame=needs_loop_frames,
        reference_images=False,
    )


async def _resolve_action_video_plan(
    user_id: int,
    *,
    needs_loop_frames: bool,
    duration_seconds: float,
) -> tuple[float, list[FrozenMediaProvider]]:
    """按链序选择不短于设计的最小时长，再冻结支持该时长的后续供应商。"""
    if (
        not 1 <= duration_seconds <= ABSOLUTE_MAX_DURATION_SECONDS
        or abs(duration_seconds - round(duration_seconds)) > 1e-6
    ):
        raise VideoPackStateError("动作时长须为 1–15 的整秒数")
    requested = int(round(duration_seconds))
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen")
    configured = [(config, build_provider(config, VideoGenProvider)) for config in chain]
    for index, (_config, provider) in enumerate(configured):
        available = provider.durations if provider.durations is not None else (requested,)
        seconds = min(
            (
                value
                for value in available
                if requested <= value <= ABSOLUTE_MAX_DURATION_SECONDS
                and _action_resolution(provider, needs_loop_frames=needs_loop_frames, duration_seconds=value)
                is not None
            ),
            default=None,
        )
        if seconds is None:
            continue
        providers: list[FrozenMediaProvider] = []
        for config, current in configured[index:]:
            resolution = _action_resolution(current, needs_loop_frames=needs_loop_frames, duration_seconds=seconds)
            if resolution is not None:
                frozen = FrozenMediaProvider.from_config(config)
                frozen.video_resolution = resolution
                providers.append(frozen)
        return float(seconds), providers
    capability = "首尾帧" if needs_loop_frames else "首帧"
    raise VideoPackStateError(f"请配置支持{capability}、声明最高可用分辨率及 {requested}–15 秒内可用时长的视频模型")


def _freeze_action_video_plan(
    job: CompanionAction,
    duration_seconds: float,
    providers: list[FrozenMediaProvider],
) -> None:
    state = (
        MediaChainState.model_validate_json(job.generation_state_json)
        if job.generation_state_json
        else MediaChainState()
    )
    state.providers = providers
    job.target_duration_seconds = duration_seconds
    job.generation_state_json = state.model_dump_json()


async def _prepare_action_video_plan(job: CompanionAction) -> None:
    """脚本付费前固定本次规格，后续步骤沿用同一生成计划。"""
    if job.media_type == "image":
        return
    if job.generation_state_json and MediaChainState.model_validate_json(job.generation_state_json).providers:
        return
    spec = _action_spec(job)
    if not isinstance(spec, VideoActionSpec):
        raise VideoPackStateError("视频动作缺少视频规格")
    seconds, providers = await _resolve_action_video_plan(
        job.user_id,
        needs_loop_frames=spec.clip_kind == "loop",
        duration_seconds=spec.duration_seconds,
    )
    _freeze_action_video_plan(job, seconds, providers)
    await _advance_job(
        job.id,
        stage=job.stage,
        target_duration_seconds=job.target_duration_seconds,
        generation_state_json=job.generation_state_json,
    )


async def _prepare_pack_identity(pack: CompanionActionPack, context: GenerationContext) -> GenerationContext:
    if context.reference_alignment == "ready":
        return context
    source = await _process_thread(_image_data_uri, _artifact_abs_path(context.identity_reference_path))
    outfit_reference = await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))

    async def save_progress(state: ImageChainState) -> None:
        context.reference_chain = state
        context.reference_alignment = "running"
        async with SESSION_LOCAL() as db:
            row = await db.get(CompanionActionPack, pack.id)
            if row is None or row.status != "processing":
                raise _GenerationRowGoneError
            row.context_json = context.model_dump_json()
            await db.commit()

    path = await align_character_reference(
        pack.user_id,
        outfit_reference,
        context.identity,
        "",
        identity_reference=source,
        storage_directory=_pack_dir(pack),
        state=context.reference_chain,
        save_progress=save_progress,
        before_submit=lambda: _require_new_paid_step(pack, context),
    )
    previous_path = pack.reference_path
    async with SESSION_LOCAL() as db:
        row = await db.get(CompanionActionPack, pack.id)
        if row is None or row.status != "processing":
            raise VideoPackStateError("视频任务已失效")
        context.reference_alignment = "ready"
        row.reference_path = path
        row.context_json = context.model_dump_json()
        await retire_action_assets(db, pack.user_id, [previous_path])
        await db.commit()
    pack.reference_path = path
    pack.context_json = context.model_dump_json()
    return context


def _action_spec(job: CompanionAction) -> ActionSpec:
    """读取原设计意图；视频的实际制作时长由生成计划绑定。"""
    if job.system_slot:
        if job.media_type == "image":
            return ImageActionSpec(action=job.key, system_slot=job.system_slot)
        return VideoActionSpec(
            action=job.key,
            system_slot=job.system_slot,
            duration_seconds=_SYSTEM_ACTION_SECONDS,
            clip_kind="loop",
        )
    design = safe_json_loads(job.source_design_json or "{}", default={})
    if not isinstance(design, dict) or not design:
        raise VideoPackError("动态动作缺少提案规格，请重做")
    return VideoActionSpec(
        action=job.key,
        name=str(design.get("name", job.name or job.key)),
        semantics=str(design.get("motion_description", job.motion_description or "")),
        use_when=list(design.get("use_when") or []),
        avoid_when=list(design.get("avoid_when") or []),
        duration_seconds=float(design.get("duration_seconds", job.target_duration_seconds or 2.0) or 2.0),
        clip_kind=str(design.get("clip_kind", job.kind or "once") or "once"),
    )


async def _compose_scripts(
    pack: CompanionActionPack,
    context: GenerationContext,
    jobs: Sequence[CompanionAction],
) -> list[ActionScriptEntry]:
    """按媒体类型撰写并逐批保存描述，续跑复用已完成批次；返回顺序与 jobs 一致。"""
    image_specs: list[ImageActionSpec] = []
    video_specs: list[VideoActionSpec] = []
    for job in jobs:
        spec = _action_spec(job)
        values: dict[str, object] = {"feedback": context.action_feedback.get(job.key, "")}
        if isinstance(spec, VideoActionSpec):
            values["duration_seconds"] = job.target_duration_seconds
            video_specs.append(spec.model_copy(update=values))
        else:
            image_specs.append(spec.model_copy(update=values))
    reference = await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))
    entries: dict[str, ActionScriptEntry] = {}
    jobs_by_key = {job.key: job for job in jobs}

    async def save_entries(batch: Sequence[ActionScriptEntry]) -> None:
        # 下一批付费调用可能暂停或失败，先保存这一批已完成的描述。
        scripts = {entry.action: entry.model_dump_json() for entry in batch}
        async with SESSION_LOCAL() as db:
            for key, script in scripts.items():
                saved_id = await db.scalar(
                    update(CompanionAction)
                    .where(CompanionAction.id == jobs_by_key[key].id)
                    .values(script_json=script)
                    .returning(CompanionAction.id),
                )
                if saved_id is None:
                    raise _GenerationRowGoneError
            await db.commit()
        for entry in batch:
            jobs_by_key[entry.action].script_json = scripts[entry.action]
            entries[entry.action] = entry

    if image_specs:
        images = await compose_action_image_descriptions(
            pack.user_id,
            reference_image=reference,
            identity=context.identity,
            persona_definition=context.persona_definition,
            personality_tags=context.personality_tags,
            outfit_description=context.outfit_description,
            specs=image_specs,
            feedback=context.feedback,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        await save_entries(images.actions)
    if video_specs:
        videos = await compose_action_script(
            pack.user_id,
            reference_image=reference,
            identity=context.identity,
            persona_definition=context.persona_definition,
            personality_tags=context.personality_tags,
            outfit_description=context.outfit_description,
            specs=video_specs,
            feedback=context.feedback,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        await save_entries(videos.actions)
    return [entries[job.key] for job in jobs]


async def _freeze_outfit_description(pack: CompanionActionPack, context: GenerationContext) -> GenerationContext:
    if context.outfit_description_frozen:
        return context
    if is_user_in_maintenance(pack.user_id):
        raise GenerationWorkPaused
    if pack.outfit_id is not None:
        with contextlib.suppress(TimeoutError):
            await wait_outfit_description(pack.user_id, pack.outfit_id)
    async with SESSION_LOCAL() as db:
        row = await db.get(CompanionActionPack, pack.id)
        if row is None:
            raise _GenerationRowGoneError
        outfit = await db.get(CompanionOutfit, row.outfit_id) if row.outfit_id is not None else None
        description = (outfit.description or "") if outfit else ""
        context = context.model_copy(update={"outfit_description": description, "outfit_description_frozen": True})
        row.context_json = context.model_dump_json()
        row.outfit_snapshot = json.dumps({"description": description}, ensure_ascii=False)
        await db.commit()
    return context


async def _generate_pack(pack_id: int) -> None:
    """成功动作独立落盘；重启及重试不重做已成功动作，不重复提交未知付费请求。"""
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None or pack.status != "processing":
                return
            jobs = (
                (
                    await db.execute(
                        select(CompanionAction).where(CompanionAction.pack_id == pack_id).order_by(CompanionAction.id),
                    )
                )
                .scalars()
                .all()
            )
        context = _load_generation_context(pack)
        if context is None:
            raise VideoPackError("动作包的制作资料不完整，请重新生成动作包")
        context = await _freeze_outfit_description(pack, context)
        pending = [job for job in jobs if job.status != "succeeded" and job.status != "failed" and not job.script_json]
        for job in pending:
            await _prepare_action_video_plan(job)
        if context.reference_alignment != "ready" or pending:
            # 参考校准与脚本撰写都是付费调用：抠像模型缺失时整包在付费前失败，已有进度留待续跑。
            require_action_matting_model()
        context = await _prepare_pack_identity(pack, context)
        must_actions = _must_succeed_actions(context.must_actions)
        if pending:
            await _emit_pack_event(
                pack.user_id,
                "companion.video.progress",
                {"packId": pack_id, "outfitId": pack.outfit_id, "stage": "script"},
            )
            await _compose_scripts(pack, context, pending)
        for job in jobs:
            if job.status in ("succeeded", "failed", "review"):
                continue
            try:
                entry = parse_action_script_entry(job.script_json or "")
                await _run_action_pipeline(pack, job, entry, context)
            except GenerationWorkPaused:
                continue
            except Exception as exc:  # noqa: BLE001 — 动作失败隔离，其他已请求动作仍可交付并独立重做
                await _record_action_failure(pack_id, job, exc)
        async with SESSION_LOCAL() as db:
            rows = (await db.execute(select(CompanionAction).where(CompanionAction.pack_id == pack_id))).scalars().all()
        # 阻塞发布：must_actions（必需 + 本版本必须成功）未完成。继承的其他未知失败不阻塞。
        blocking = [job for job in rows if accepted_action_asset(job) is None and job.key in must_actions]
        if blocking:
            if is_user_in_maintenance(pack.user_id):
                return
            # 各动作原因已写入动作行；包级文案只汇总不重复的原因，不暴露内部动作键。
            await _fail_pack(pack_id, "；".join(dict.fromkeys(job.error or "动作未完成" for job in blocking)))
            return
        results = {
            job.key: ActionResult.model_validate_json(material.result_json)
            for job in rows
            if (material := accepted_action_asset(job)) is not None
        }
        await _emit_pack_event(
            pack.user_id,
            "companion.video.progress",
            {"packId": pack_id, "outfitId": pack.outfit_id, "stage": "publish"},
        )
        await _publish_ready(pack_id, results)
    except GenerationWorkPaused:
        return
    except _GenerationRowGoneError:
        logger.info("video pack left generation", extra={"pack_id": pack_id})
    except Exception as exc:  # noqa: BLE001 — 后台异常必须转为持久可见失败
        logger.exception("video pack generation failed", extra={"pack_id": pack_id})
        await _fail_pack(pack_id, _generation_error(exc))


async def _record_action_failure(pack_id: int, job: CompanionAction, exc: Exception) -> None:
    """单动作失败落库，不影响同批其他动作；任务行已删除时只记录并跳过。"""
    extra = {"pack_id": pack_id, "action_id": job.id, "action": job.key}
    if isinstance(exc, _GenerationRowGoneError):
        logger.info("video action row removed during generation", extra=extra)
        return
    logger.error("video action failed", extra=extra, exc_info=exc)
    reason, _ = media_failure_reason(exc)
    status = "result_unknown" if reason == "result_unknown" else "failed"
    if isinstance(exc, ActionMaterialRejectedError):
        job.stage = "invalid_asset"
    try:
        await _advance_job(
            job.id,
            stage=job.stage,
            status=status,
            error=_generation_error(exc, media_type=job.media_type),
        )
    except _GenerationRowGoneError:
        logger.info("video action row removed before its failure was recorded", extra=extra)


def _generation_error(exc: Exception, *, media_type: str = "video") -> str:
    reason, _ = media_failure_reason(exc)
    if reason in {"result_unknown", "content_policy_blocked"} or isinstance(
        exc,
        (ProviderError, MediaProviderFailedError),
    ):
        if media_type == "image":
            return {
                "result_unknown": "图片提交结果尚未确认，请先核对已有结果",
                "content_policy_blocked": "图片生成被供应商拒绝，请调整姿态要求后重做",
            }.get(reason, "图片生成服务暂不可用，可重试已有任务或素材")
        return video_failure_message(reason)
    if isinstance(
        exc,
        (
            VideoProcessError,
            VideoPackError,
            VideoScriptError,
            ImageGenerationError,
            VisualReasoningError,
            LlmCallBlockedError,
        ),
    ):
        return str(exc)[:500]
    return "动作处理失败，可重试已有任务或素材"


async def _prepare_action_pose(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: ActionScriptEntry,
    context: GenerationContext,
) -> None:
    if job.pose_path and _artifact_abs_path(job.pose_path).is_file():
        return
    require_action_matting_model()
    reference_uri = await _process_thread(_image_data_uri, _artifact_abs_path(pack.reference_path))
    identity_uri = await _process_thread(_image_data_uri, _artifact_abs_path(context.identity_reference_path))
    pose_state = (
        ImageChainState.model_validate_json(job.pose_generation_state_json)
        if job.pose_generation_state_json
        else ImageChainState()
    )

    async def save_pose(progress: ImageChainState) -> None:
        job.pose_generation_state_json = progress.model_dump_json(exclude_none=True)
        job.stage = "pose"
        await _advance_job(job.id, stage="pose", pose_generation_state_json=job.pose_generation_state_json)

    prompt = (
        build_action_image_prompt(entry, context.identity)
        if isinstance(entry, ImageActionScriptEntry)
        else build_pose_prompt(entry, context.identity)
    )
    paths = await generate_character_images(
        prompt,
        user_id=pack.user_id,
        storage_directory=_pack_dir(pack),
        reference_image=reference_uri,
        identity_reference=identity_uri,
        identity_text=render_character_profile(context.identity),
        size=FULLBODY_SIZE,
        image_edit=True,
        prefer_transparent_background=True,
        state=pose_state,
        save_progress=save_pose,
        before_submit=lambda: _require_new_paid_step(pack, context),
    )
    job.pose_path = action_pose_asset_path(
        pack.user_id,
        pose_state.generation_id,
        directory=_pack_dir(pack),
    )
    await _advance_job(job.id, stage="pose", pose_path=job.pose_path)
    pose_data = await _process_thread(_artifact_abs_path(paths[0]).read_bytes)
    prepared = await _process_thread(prepare_action_frame, pose_data, preserve_resolution=entry.media_type == "image")
    await save_action_pose_asset_async(
        prepared,
        user_id=pack.user_id,
        generation_id=pose_state.generation_id,
        directory=_pack_dir(pack),
    )


async def _run_image_pipeline(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: ImageActionScriptEntry,
    context: GenerationContext,
) -> bool:
    if job.result_json:
        result = ActionResult.model_validate_json(job.result_json)
        if result.clip.media_type != "image" or any(
            not _artifact_abs_path(path).is_file()
            for path in (result.clip.path, result.cover_path, result.hitmask_path)
        ):
            raise VideoPackError("已保存的动作图片不可读，请恢复原素材")
    else:
        await _prepare_action_pose(pack, job, entry, context)
        await _advance_job(job.id, stage="process")
        await _emit_pack_event(
            pack.user_id,
            "companion.video.progress",
            {"packId": pack.id, "stage": "process", "action": job.key},
        )
        if not job.pose_path:
            raise VideoPackError("动作图片不可读")
        with tempfile.TemporaryDirectory(prefix="action-image-") as tmp:
            canvas = json.loads(pack.canvas_spec)
            result = await _prepare_image(
                Path(tmp),
                job.key,
                _artifact_abs_path(job.pose_path),
                pack.user_id,
                canvas["width"],
                canvas["height"],
                directory=_pack_dir(pack),
            )
        values = _clip_row_values(result)
        await _advance_job(job.id, stage="process", **values)
        for key, value in values.items():
            setattr(job, key, value)
        job.stage = "process"
    review_id = None
    if pack.status == "ready":
        identity_uri = await _process_thread(_image_data_uri, _artifact_abs_path(context.identity_reference_path))
        verdict, reason = await review_character_frames(
            pack.user_id,
            identity_uri,
            await _action_sample_uris(result.clip),
            image=True,
            identity_text=render_character_profile(context.identity),
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        if verdict == "review":
            review_id = await create_media_review(
                pack.user_id,
                "image",
                result.clip.path,
                reason,
                publication=MediaReviewPublication(
                    kind="action",
                    pack_id=pack.id,
                    action_id=job.id,
                    title=job.name,
                    system_slot=job.system_slot,
                ),
            )
    pose_state = (
        ImageChainState.model_validate_json(job.pose_generation_state_json)
        if job.pose_generation_state_json
        else ImageChainState()
    )
    best = pose_state.best()
    provider = pose_state.providers[best.attempt] if best is not None else None
    await _advance_job(
        job.id,
        stage="publish",
        status="review" if review_id else "succeeded",
        **_clip_row_values(result),
        kind=None,
        loopable=None,
        target_duration_seconds=None,
        generation_state_json=None,
        artifact_path=None,
        provider_task_id=None,
        provider=provider.provider if provider else job.provider,
        model=provider.model if provider else job.model,
    )
    return review_id is None


async def _run_action_pipeline(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: ActionScriptEntry,
    context: GenerationContext,
) -> bool:
    """按媒体类型制作与复核；整包与单动作共用，返回是否直接成功。"""
    if isinstance(entry, ImageActionScriptEntry):
        if job.media_type != "image":
            raise VideoPackStateError("动作图片规格与素材类型不符")
        return await _run_image_pipeline(pack, job, entry, context)
    if job.media_type != "video":
        raise VideoPackStateError("视频规格与素材类型不符")
    await _prepare_action_video_plan(job)
    state = MediaChainState.model_validate_json(job.generation_state_json or "")
    identity_uri = await _process_thread(_image_data_uri, _artifact_abs_path(context.identity_reference_path))
    if (
        state.phase == "ready"
        and state.active_index is None
        and state.needs_next()
        and (not job.pose_path or not _artifact_abs_path(job.pose_path).is_file())
    ):
        await _prepare_action_pose(pack, job, entry, context)

    if state.phase == "submitting":
        state.stop_reason = "result_unknown"
        await _save_action_state(job, state)
    while state.phase != "complete" and not state.stop_reason:
        if state.phase == "ready":
            if not state.needs_next():
                break
            await _require_new_paid_step(pack, context)
            try:
                require_action_matting_model()
            except VideoPackStateError:
                if state.best() is None:
                    raise
                # 抠像模型缺失时不再追加付费提交，按已有最佳候选收尾。
                state.stop_reason = "matting_unavailable"
                await _save_action_state(job, state)
                break
            index = state.next_index
            config = await resolve_frozen_media_provider(pack.user_id, "video_gen", state.providers[index])
            if config is None or not state.providers[index].video_resolution:
                state.next_index += 1
                await _save_action_state(job, state)
                continue
            state.begin(index)
            job.provider, job.model = config.provider_name, config.model
            job.provider_task_id = job.artifact_path = None
            await _save_action_state(
                job,
                state,
                stage="submit",
                provider=job.provider,
                model=job.model,
                provider_task_id=None,
                artifact_path=None,
            )
        try:
            if state.phase != "evaluating":
                source_path, result = await _run_action_attempt(pack, job, entry, context, state)
                state.candidates.append(
                    MediaCandidate(
                        path=result.clip.path,
                        attempt=state.active_index or 0,
                        result_json=result.model_dump_json(),
                        artifacts=[source_path, result.clip.path, result.cover_path, result.hitmask_path],
                    ),
                )
                state.phase = "evaluating"
                await _save_action_state(job, state)
            candidate = next(item for item in state.candidates if item.attempt == state.active_index)
            if not candidate.evaluated:
                result = ActionResult.model_validate_json(candidate.result_json or "")
                try:
                    score = await score_character_frames(
                        pack.user_id,
                        identity_uri,
                        await _clip_frame_uris(result.clip),
                        identity_text=render_character_profile(context.identity),
                        before_submit=lambda: _require_new_paid_step(pack, context),
                    )
                except LlmCallBlockedError:
                    raise
                except Exception:
                    logger.warning("action identity score unavailable", extra={"action_id": job.id}, exc_info=True)
                    score = None
                state.accept_score(candidate, score)
            state.phase = "ready"
            await _save_action_state(job, state)
        except GenerationWorkPaused:
            raise
        except Exception as exc:
            reason, can_continue = media_failure_reason(exc)
            # 只有明确未提交成功的 API 错误允许技术回退；轮询、下载、后处理继续原任务。
            if can_continue and (state.phase == "submitting" or isinstance(exc, MediaProviderFailedError)):
                state.phase = "ready"
                if state.next_index >= len(state.providers):
                    state.stop_reason = reason
                await _save_action_state(job, state)
                continue
            if state.best() is None and state.phase != "submitting":
                await _save_action_state(job, state)
                raise
            state.stop_reason = reason
            await _save_action_state(job, state)
            if state.best() is None:
                raise
            break
    best = state.best()
    if best is None:
        if state.stop_reason == "result_unknown":
            raise ProviderResultUnknownError("POST", "video_gen")
        if state.stop_reason:
            raise MediaProviderFailedError(video_failure_message(state.stop_reason), reason=state.stop_reason)
        raise VideoPackError("视频生成服务未返回可用素材")
    unused_source = job.artifact_path
    result = ActionResult.model_validate_json(best.result_json or "")
    spec = result.clip
    review_id = None
    frames: tuple[str, ...] = ()
    if job.system_slot in ("peek_left", "peek_right") or pack.status == "ready":
        try:
            frames = await _clip_frame_uris(spec)
        except Exception:
            logger.warning("video action frame sampling failed", extra={"action_id": job.id}, exc_info=True)

    if job.system_slot in ("peek_left", "peek_right"):
        geometry = await inspect_peek_geometry(
            pack.user_id,
            job.system_slot,
            identity_uri,
            frames,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        spec = spec.model_copy(update={"peek_geometry": geometry})
        result = result.model_copy(update={"clip": spec})

    if pack.status == "ready":
        try:
            if not frames:
                raise VideoPackError("动作视频画面无法读取")
            verdict, reason = await review_character_frames(
                pack.user_id,
                identity_uri,
                frames,
                identity_text=render_character_profile(context.identity),
                before_submit=lambda: _require_new_paid_step(pack, context),
            )
        except LlmCallBlockedError:
            raise
        except Exception:
            verdict, reason = "review", "动作视频的自动检查未完成，请预览确认"
        if verdict == "review":
            review_id = await create_media_review(
                pack.user_id,
                "video",
                spec.path,
                reason,
                publication=MediaReviewPublication(
                    kind="action",
                    pack_id=pack.id,
                    action_id=job.id,
                    title=job.name,
                    system_slot=job.system_slot,
                ),
            )
    state.finish()
    await _save_action_state(
        job,
        state,
        stage="publish",
        status="review" if review_id else "succeeded",
        **_clip_row_values(result),
        artifact_path=best.artifacts[0],
        provider=state.providers[best.attempt].provider,
        model=state.providers[best.attempt].model,
        provider_task_id=None,
        kind=entry.clip_kind,
        target_duration_seconds=entry.duration_seconds,
        loopable=entry.clip_kind == "loop",
        error="后续提交结果未确认，已保留最佳素材" if state.stop_reason == "result_unknown" else None,
    )
    keep = set(best.artifacts) | {pack.reference_path, context.identity_reference_path, job.pose_path}
    if unused_source and unused_source not in keep:
        await asyncio.to_thread(unlink_companion_asset, unused_source)
    for candidate in state.candidates:
        for path in candidate.artifacts:
            if path not in keep:
                await asyncio.to_thread(unlink_companion_asset, path)
    return review_id is None


async def _run_action_attempt(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: VideoActionScriptEntry,
    context: GenerationContext,
    state: MediaChainState,
) -> tuple[str, ActionResult]:
    """单家供应商尝试：提交或续轮询 → 下载源视频 → 抠像与接点验收；返回 (源视频路径, 处理结果)。"""
    attempt = state.active_index
    if attempt is None:
        raise VideoPackError("动作制作进度无效，请重新生成")

    async def progress(stage: str) -> None:
        job.stage = stage
        await _advance_job(job.id, stage=stage)
        await _emit_pack_event(
            pack.user_id,
            "companion.video.progress",
            {"packId": pack.id, "outfitId": pack.outfit_id, "stage": stage, "action": job.key},
        )

    if not job.artifact_path and state.source_path and _artifact_abs_path(state.source_path).is_file():
        job.artifact_path = state.source_path
        await _advance_job(job.id, stage="process", artifact_path=job.artifact_path)
    source_path = job.artifact_path
    if not source_path:
        if not state.result_url:
            config = await resolve_frozen_media_provider(pack.user_id, "video_gen", state.providers[attempt])
            if config is None:
                raise VideoPackError("视频供应商配置已变更，原任务无法继续")
            provider = build_provider(config, VideoGenProvider)
            task_id = job.provider_task_id
            if not task_id:
                resolution = state.providers[attempt].video_resolution
                if not resolution:
                    raise VideoPackStateError("动作制作资料缺少分辨率，请重新生成该动作")
                if not job.pose_path or not _artifact_abs_path(job.pose_path).is_file():
                    raise VideoPackError("动作起始姿态图不可读，请恢复原素材")
                pose_uri = await _process_thread(_action_video_frame_uri, _artifact_abs_path(job.pose_path))

                async def submit(current: VideoGenProvider) -> VideoJobStatus:
                    await _require_new_paid_step(pack, context)
                    status = await current.submit(
                        VideoGenRequest(
                            prompt=build_video_prompt(entry, context.identity),
                            duration=int(round(entry.duration_seconds)),
                            resolution=resolution,
                            first_frame_image=pose_uri,
                            last_frame_image=pose_uri if entry.clip_kind == "loop" else None,
                        ),
                    )
                    if not status.task_id:
                        raise ProviderResultUnknownError("POST", config.base_url)
                    return status

                try:
                    submitted = await execute_with_fallback([config], VideoGenProvider, submit, user_id=pack.user_id)
                except GenerationWorkPaused:
                    # 尚未提交的游标回到可安全提交的位置，维护恢复不能把它判为结果未知。
                    state.phase, state.active_index, state.next_index = "ready", None, attempt
                    await _save_action_state(job, state)
                    raise
                task_id = job.provider_task_id = submitted.task_id
                state.phase = "processing"
                await _save_action_state(job, state, stage="generate", provider_task_id=task_id)
            if state.phase != "storing":
                await progress("generate")
                try:
                    status = await poll_video_task(provider, task_id)
                except VideoPollTimeoutError as exc:
                    raise VideoPackError("视频生成超时，请稍后重试") from exc
                if status.status != "succeeded":
                    reason = video_provider_failure_reason(status.error)
                    raise MediaProviderFailedError(video_failure_message(reason), reason=reason)
                state.result_url = status.download_url
                state.phase = "storing"
                await _save_action_state(job, state, stage="download")
        await progress("download")
        if not state.result_url:
            raise VideoPackError("供应商未返回视频下载地址")
        data = await download_media_result(state.result_url, max_bytes=MAX_SOURCE_BYTES, timeout=180)
        ext = sniff_media_ext(data)
        if ext not in _SOURCE_MEDIA_EXTS:
            raise VideoPackError("供应商返回了不支持的视频格式")
        # 源视频路径先随进度落库，写盘中断后按确定性路径复用。
        state.source_path = action_source_asset_path(
            pack.user_id,
            state.generation_id,
            attempt,
            ext,
            directory=_pack_dir(pack),
        )
        await _save_action_state(job, state, stage="download")
        source_path = await save_action_source_asset_async(
            data,
            user_id=pack.user_id,
            generation_id=state.generation_id,
            directory=_pack_dir(pack),
            attempt=attempt,
            ext=ext,
        )
        job.artifact_path = source_path
        await _advance_job(job.id, stage="process", artifact_path=source_path)
    await progress("process")
    with tempfile.TemporaryDirectory(prefix="video-action-") as tmp:
        work = Path(tmp)
        matte = work / "matte.mkv"
        await _process_thread(matte_video, _artifact_abs_path(source_path), matte)
        await _process_thread(validate_action_clip, matte)
        canvas = json.loads(pack.canvas_spec)
        result = await _prepare_clip(
            work,
            job.key,
            matte,
            pack.user_id,
            canvas["width"],
            canvas["height"],
            preserve_resolution=True,
            directory=_pack_dir(pack),
        )
    return source_path, result


def _start_pack_task(
    pack_id: int,
    user_id: int,
    coro: Coroutine[object, object, bool | None],
    *,
    name: str,
    drain_queue_after: bool,
) -> None:
    """登记包级生成任务；收尾时按唤醒信号或本轮进展继续消费该包的排队动作。"""
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    _GEN_INFLIGHT.add(pack_id)
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def _done(done: asyncio.Task[bool | None]) -> None:
        _GEN_INFLIGHT.discard(pack_id)
        pending = pack_id in _GEN_PENDING
        _GEN_PENDING.discard(pack_id)
        if done.cancelled():
            return
        if (exc := done.exception()) is not None:
            logger.error("video pack task crashed", extra={"pack_id": pack_id, "task": name}, exc_info=exc)
            return
        # 单动作失败不阻塞其他排队项；整批无进展时停止，避免空转或反复恢复未知结果。
        if pending or drain_queue_after or done.result():
            _kick_dynamic_generation(pack_id, user_id, queued_only=True)
        else:
            _kick_retire(pack_id, user_id)

    task.add_done_callback(_done)


def _kick_retire(pack_id: int, user_id: int) -> None:
    """包上制作收尾后补做被在途任务推迟的旧版本退役。"""
    task = asyncio.create_task(_retire_after_generation(pack_id, user_id), name=f"companion.video.retire.{pack_id}")
    _TASKS.add(
        task,
        on_error=lambda t: logger.error("video pack retire failed", extra={"pack_id": pack_id}, exc_info=t.exception()),
    )
    track_user_task(user_id, task)


def _kick_generate(pack_id: int, user_id: int) -> None:
    if pack_id in _GEN_INFLIGHT:
        return
    _start_pack_task(
        pack_id,
        user_id,
        _generate_pack(pack_id),
        name=f"companion.video.generate.{pack_id}",
        drain_queue_after=False,
    )


async def _queue_in_place_redo(
    db: AsyncSession,
    pack: CompanionActionPack,
    action: str,
    feedback: str,
) -> CompanionAction:
    """ready 包上系统动作原地重做：保留动作身份与元数据，只重置素材生成状态；仍待确认的旧成品视为未采纳，其复核项随之结束。"""
    job = (
        await db.execute(
            select(CompanionAction).where(CompanionAction.pack_id == pack.id, CompanionAction.key == action),
        )
    ).scalar_one_or_none()
    # 在途制作持有该行或正在消费同包队列：此时重置会被覆盖或丢失，并丢弃已付费的任务句柄。
    if pack.id in _GEN_INFLIGHT or (job is not None and job.status in ("queued", "processing")):
        raise VideoPackStateError("动作仍在制作中，请等待完成后再重新生成")
    media_type = SYSTEM_ACTION_MEDIA_TYPES[action]
    seconds: float | None = None
    providers: list[FrozenMediaProvider] = []
    if media_type == "video":
        seconds, providers = await _resolve_action_video_plan(
            pack.user_id,
            needs_loop_frames=True,
            duration_seconds=_SYSTEM_ACTION_SECONDS,
        )
    if job is None:
        job = CompanionAction(
            user_id=pack.user_id,
            pack_id=pack.id,
            outfit_id=pack.outfit_id,
            key=action,
            system_slot=action,
            name="",
            media_type=media_type,
            kind="loop" if media_type == "video" else None,
            status="queued",
            stage="design",
            reference_hash=pack.reference_hash,
        )
        db.add(job)
    else:
        await reject_pending_action_reviews(db, job)
        await retire_action_assets(db, pack.user_id, action_asset_paths(job))
        clear_action_attempt(job)
        job.status = "queued"
        job.stage = "design"
        job.error = None
        job.metadata_revision += 1
    job.media_type = media_type
    if media_type == "image":
        job.kind = job.target_duration_seconds = job.actual_duration_ms = job.frames = job.loopable = (
            job.hitmask_fps
        ) = None
    elif seconds is not None:
        job.kind = "loop"
        _freeze_action_video_plan(job, seconds, providers)
    context = _load_generation_context(pack)
    if context is not None:
        context.action_feedback[action] = feedback.strip()[:1000]
        pack.context_json = context.model_dump_json()
    await db.flush()
    return job


def kick_dynamic_action(pack_id: int, action_id: int, user_id: int) -> None:
    """启动已落库的单动作任务；同包在途时由其收尾消费队列。"""
    _kick_dynamic_generation(pack_id, user_id, action_ids=[action_id])


def _kick_dynamic_generation(
    pack_id: int,
    user_id: int,
    *,
    action_ids: list[int] | None = None,
    queued_only: bool = False,
) -> None:
    # 用户维护期间不启动新的制作，也不记唤醒；排队动作保持 queued，维护结束后由 resume_user_dynamic_actions 续跑。
    if is_user_in_maintenance(user_id):
        return
    if pack_id in _GEN_INFLIGHT:
        # 空队列查询与任务收尾之间也可能提交新动作，唤醒信号须保留到收尾。
        _GEN_PENDING.add(pack_id)
        return
    _start_pack_task(
        pack_id,
        user_id,
        _generate_dynamic_actions(pack_id, action_ids=action_ids, queued_only=queued_only),
        name=f"companion.video.dynamic.{pack_id}",
        drain_queue_after=action_ids is not None,
    )


async def _generate_dynamic_actions(
    pack_id: int,
    *,
    action_ids: list[int] | None = None,
    queued_only: bool = False,
) -> bool:
    """按冻结规格制作 ready 包中的动作，随后发布目录；返回本轮是否处理了动作。"""
    try:
        async with SESSION_LOCAL() as db:
            pack = await db.get(CompanionActionPack, pack_id)
            if pack is None or pack.status != "ready":
                return False
            # 结果未知的动作不再自动推进，只能由用户明确重做，避免每次唤醒或重启都翻转状态并空推目录。
            stmt = select(CompanionAction).where(
                CompanionAction.pack_id == pack_id,
                CompanionAction.status.in_(("queued",) if queued_only else ("queued", "processing")),
            )
            if action_ids is not None:
                stmt = stmt.where(CompanionAction.id.in_(action_ids))
            jobs = (await db.execute(stmt.order_by(CompanionAction.id))).scalars().all()
            if not jobs:
                return False
        context = _load_generation_context(pack)
        if context is None:
            await _fail_dynamic_jobs(pack_id, "该动作包的制作资料不完整，请重新生成动作包", action_ids=action_ids)
            return False
        succeeded = False
        for job in jobs:
            # 维护期间不再开始排队动作；已在制作的动作继续收敛。
            if job.status == "queued" and is_user_in_maintenance(pack.user_id):
                continue
            try:
                if await _generate_one_dynamic(pack, job, context):
                    succeeded = True
            except GenerationWorkPaused:
                continue
            except Exception as exc:  # noqa: BLE001 — 单动作失败隔离，不影响已就绪目录
                await _record_action_failure(pack_id, job, exc)
        # 只有新成功的动作改变目录：发布新目录快照（失败与待复核动作不并入），并兑现这些动作制作期间的表达意图。
        if succeeded:
            await _publish_dynamic_catalog(pack_id)
            await _fulfill_pending_intents([job.id for job in jobs])
        return not is_user_in_maintenance(pack.user_id)
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底：失败必须落库可见
        logger.exception("dynamic action generation crashed", extra={"pack_id": pack_id})
        await _fail_dynamic_jobs(pack_id, _generation_error(exc), action_ids=action_ids)
        return False


async def _generate_one_dynamic(
    pack: CompanionActionPack,
    job: CompanionAction,
    context: GenerationContext,
) -> bool:
    """复用已保存脚本，缺失时按冻结规格撰写并落库，再交给素材管线；返回素材是否直接成功。"""
    await _prepare_action_video_plan(job)
    if job.script_json:
        entry = parse_action_script_entry(job.script_json)
    else:
        # 撰写脚本是本次制作的首个付费调用。
        require_action_matting_model()
        await _emit_pack_event(pack.user_id, "companion.action.job_updated", {"packId": pack.id, "stage": "script"})
        (entry,) = await _compose_scripts(pack, context, [job])
    return await _run_action_pipeline(pack, job, entry, context)


async def _fail_dynamic_jobs(pack_id: int, message: str, *, action_ids: list[int] | None = None) -> None:
    async with SESSION_LOCAL() as db:
        stmt = (
            update(CompanionAction)
            .where(
                CompanionAction.pack_id == pack_id,
                CompanionAction.status == "queued",
            )
            .values(status="failed", error=message[:500])
            .execution_options(synchronize_session=False)
        )
        if action_ids is not None:
            stmt = stmt.where(CompanionAction.id.in_(action_ids))
        await db.execute(stmt)
        await db.commit()


async def _publish_dynamic_catalog(pack_id: int) -> None:
    """动态动作完成后发布动作目录快照（CAS），并广播目录变更。"""
    async with SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None:
            return
        if await _publish_catalog(db, pack) is None:
            return
        emit_catalog_changed(db, pack)
        await db.commit()


async def _fulfill_pending_intents(action_ids: list[int]) -> None:
    """补发本轮动作制作期间保存、仍在有效期内的表达意图；只兑现本轮目标，已就绪动作的未执行即时请求不借此补播。"""
    async with SESSION_LOCAL() as db:
        for action_id in action_ids:
            await fulfill_deferred_play_intents(db, action_id)
        await db.commit()


async def drain_video_generation() -> None:
    """停机时取消并等待构建与生成任务；已提交的供应商任务由重启恢复凭句柄续轮询。"""
    await _TASKS.drain()


async def resume_video_generation_jobs() -> None:
    """进程重启恢复：生成包凭句柄续跑；上传源片段不可复用、中断包按失败落库由用户重交；ready 包上未完成动态动作逐包续跑。"""
    async with SESSION_LOCAL() as db:
        packs = (
            (await db.execute(select(CompanionActionPack).where(CompanionActionPack.status == "processing")))
            .scalars()
            .all()
        )
        interrupted = [pack for pack in packs if not pack.reference_path]
        for pack in interrupted:
            await _mark_pack_failed(db, pack, "处理进程重启，请重新提交")
        if interrupted:
            await db.commit()
            logger.info("resumed video packs marked failed", extra={"count": len(interrupted)})
        # ready 包上仍有 queued/processing 动态动作（含已取得供应商句柄的）：逐包恢复生成；结果未知的不自动恢复。
        dynamic_rows = (
            await db.execute(
                select(CompanionActionPack.id, CompanionActionPack.user_id)
                .join(CompanionAction, CompanionAction.pack_id == CompanionActionPack.id)
                .where(
                    CompanionActionPack.status == "ready",
                    CompanionAction.status.in_(("queued", "processing")),
                )
                .group_by(CompanionActionPack.id, CompanionActionPack.user_id),
            )
        ).all()
    for pack in packs:
        if pack.reference_path:
            _kick_generate(pack.id, pack.user_id)
    for pack_id, user_id in dynamic_rows:
        _kick_dynamic_generation(pack_id, user_id)


async def resume_user_dynamic_actions(user_id: int) -> None:
    """用户维护结束后，续跑该用户 ready 包上被维护挡下的排队动作。"""
    async with SESSION_LOCAL() as db:
        processing = (
            await db.scalars(
                select(CompanionActionPack).where(
                    CompanionActionPack.user_id == user_id,
                    CompanionActionPack.status == "processing",
                    CompanionActionPack.reference_path != "",
                ),
            )
        ).all()
        pack_ids = (
            (
                await db.execute(
                    select(CompanionActionPack.id)
                    .join(CompanionAction, CompanionAction.pack_id == CompanionActionPack.id)
                    .where(
                        CompanionActionPack.user_id == user_id,
                        CompanionActionPack.status == "ready",
                        CompanionAction.status.in_(("queued", "processing")),
                    )
                    .distinct(),
                )
            )
            .scalars()
            .all()
        )
    for pack in processing:
        _kick_generate(pack.id, user_id)
    for pack_id in pack_ids:
        _kick_dynamic_generation(pack_id, user_id)


async def ensure_system_action(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    action: str,
) -> CompanionActionPack:
    """复用当前包的探身任务，仅在槽位缺失时创建。"""
    if action not in ("peek_left", "peek_right"):
        raise VideoPackError("只支持补齐左右探身动作")

    kick_action_id: int | None = None
    republish = False
    async with get_avatar_job_lock(user_id):
        pack = (
            await db.execute(
                select(CompanionActionPack)
                .where(
                    CompanionActionPack.id == pack_id,
                    CompanionActionPack.user_id == user_id,
                    CompanionActionPack.active.is_(True),
                    CompanionActionPack.status == "ready",
                )
                .with_for_update(),
            )
        ).scalar_one_or_none()
        if pack is None:
            raise VideoPackNotFoundError("当前动作包已变化，请重新读取")
        context = _load_generation_context(pack)
        if context is None or not pack.reference_path or context.reference_alignment != "ready":
            raise VideoPackStateError("该动作包没有可用的参考图，无法自动补齐探身动作")
        if not all(
            _artifact_abs_path(path).is_file() for path in (pack.reference_path, context.identity_reference_path)
        ):
            raise VideoPackStateError("该动作包的参考图无法读取，请重新生成动作包")

        job = (
            await db.execute(
                select(CompanionAction).where(
                    CompanionAction.pack_id == pack.id,
                    CompanionAction.key == action,
                ),
            )
        ).scalar_one_or_none()
        if job is None or job.status == "queued" or (job.status == "processing" and pack.id not in _GEN_INFLIGHT):
            require_action_matting_model()
            if job is None:
                seconds, providers = await _resolve_action_video_plan(
                    user_id,
                    needs_loop_frames=True,
                    duration_seconds=_SYSTEM_ACTION_SECONDS,
                )
                job = CompanionAction(
                    user_id=user_id,
                    pack_id=pack.id,
                    outfit_id=pack.outfit_id,
                    key=action,
                    name="",
                    system_slot=action,
                    kind="loop",
                    status="queued",
                    stage="design",
                    target_duration_seconds=seconds,
                    reference_hash=pack.reference_hash,
                )
                _freeze_action_video_plan(job, seconds, providers)
                db.add(job)
                await db.flush()
            kick_action_id = job.id
        elif job.status == "succeeded":
            # 素材成功但目录发布失败时，只重试发布，不重新生成。
            republish = True
        await db.commit()

    if kick_action_id is not None:
        kick_dynamic_action(pack_id, kick_action_id, user_id)
    elif republish:
        await _publish_dynamic_catalog(pack_id)
    await db.refresh(pack)
    return pack


async def _activate_locked(db: AsyncSession, pack: CompanionActionPack) -> None:
    # 每次激活（含重新穿回同一包）推进用户级外观代次；调用方持用户锁，最大值 + 1 不会并发重复。
    latest_epoch = (
        await db.execute(
            select(func.coalesce(func.max(CompanionActionPack.appearance_epoch), 0)).where(
                CompanionActionPack.user_id == pack.user_id,
            ),
        )
    ).scalar_one()
    await db.execute(
        update(CompanionActionPack)
        .where(CompanionActionPack.user_id == pack.user_id, CompanionActionPack.active.is_(True))
        .values(active=False)
        .execution_options(synchronize_session=False),
    )
    # bulk update synchronize_session=False 后必须显式标脏，重启激活同一行也能写回。
    pack.active = True
    flag_modified(pack, "active")
    pack.appearance_epoch = latest_epoch + 1
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
    emit_catalog_changed(db, pack)


async def activate_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionActionPack:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError("找不到动作包")
        if pack.status != "ready":
            raise VideoPackStateError("动作包尚未就绪")
        if await _newer_ready_pack(db, pack) is not None:
            raise VideoPackStateError("该外观已有更新的动作包，请启用最新版本")
        outfit = await db.get(CompanionOutfit, pack.outfit_id)
        avatar = await _active_avatar(db, user_id)
        if outfit is None or await _reference_hash(outfit, avatar) != pack.reference_hash:
            raise VideoPackStateError("该动作包对应的参考已变更")
        context = _load_generation_context(pack)
        if context is not None:
            await _require_current_identity_image(context, avatar)
        if pack.identity_review == "review":
            pack.identity_review = "accepted"
        await _activate_locked(db, pack)
        _, removed = await _retire_superseded_locked(db, pack)
        await db.commit()
        spawn_removed_action_task_cancellation(user_id, removed)
        await db.refresh(pack)

    return pack


def _pack_assets(pack: CompanionActionPack, jobs: list[CompanionAction]) -> set[str]:
    paths = {pack.reference_path, pack.manifest_path, pack.cover_path or ""}
    context = _load_generation_context(pack)
    if context is not None:
        paths.add(context.identity_reference_path)
        paths.update(candidate.path for candidate in context.reference_chain.candidates)
        paths.add(context.reference_chain.pending_path or "")
    for job in jobs:
        paths |= action_asset_paths(job)
        if job.generation_state_json:
            state = MediaChainState.model_validate_json(job.generation_state_json)
            paths.add(state.source_path or "")
            paths.update(path for candidate in state.candidates for path in candidate.artifacts)
        if job.pose_generation_state_json:
            pose_state = ImageChainState.model_validate_json(job.pose_generation_state_json)
            paths.update(candidate.path for candidate in pose_state.candidates)
            paths.add(pose_state.pending_path or "")
        paths.update(
            (
                job.artifact_path or "",
                job.pose_path or "",
                job.media_path or "",
                job.cover_path or "",
                job.hitmask_path or "",
            ),
        )
        if job.result_json:
            result = ActionResult.model_validate_json(job.result_json)
            paths.update((result.clip.path, result.cover_path, result.hitmask_path))
    return paths - {""}


async def _jobs_by_pack(db: AsyncSession, *conditions: ColumnElement[bool]) -> dict[int, list[CompanionAction]]:
    rows = (await db.execute(select(CompanionAction).where(*conditions).order_by(CompanionAction.id))).scalars()
    grouped: dict[int, list[CompanionAction]] = {}
    for job in rows:
        grouped.setdefault(job.pack_id, []).append(job)
    return grouped


async def _remove_packs(
    db: AsyncSession,
    user_id: int,
    targets: list[CompanionActionPack],
) -> tuple[set[str], list[RemovedActionVideoTask]]:
    """删除目标包及任务行（调用方提交）；返回可回收资产路径和待撤销的远端视频句柄。共享资源（冻结参考、复用片段）只在最后一个引用消失才回收。"""
    packs = (
        (await db.execute(select(CompanionActionPack).where(CompanionActionPack.user_id == user_id))).scalars().all()
    )
    jobs_by_pack = await _jobs_by_pack(db, CompanionAction.user_id == user_id)
    target_ids = {pack.id for pack in targets}
    candidates: set[str] = set()
    for pack in targets:
        for job in jobs_by_pack.get(pack.id, []):
            await reject_pending_action_reviews(db, job)
        candidates |= _pack_assets(pack, jobs_by_pack.get(pack.id, []))
    for other in packs:
        if other.id not in target_ids:
            candidates -= _pack_assets(other, jobs_by_pack.get(other.id, []))
    removed = removed_video_tasks(job for pack in targets for job in jobs_by_pack.get(pack.id, []))
    for pack in targets:
        for job in jobs_by_pack.get(pack.id, []):
            await db.delete(job)
        await db.delete(pack)
    await retire_action_assets(db, user_id, candidates)
    return candidates, removed


def _load_generation_context(pack: CompanionActionPack) -> GenerationContext | None:
    """生成包的冻结上下文；上传导入包没有上下文。"""
    return GenerationContext.model_validate_json(pack.context_json) if pack.context_json else None


async def _require_current_identity_image(context: GenerationContext, avatar: AvatarAsset | None) -> None:
    """已制作的包只在身份图变化后失效：冻结的全身身份图须与当前已采纳全身图一致，角色卡文字修订不影响。"""
    if avatar is None or avatar.id != context.identity.avatar_id:
        raise VideoPackStateError("该动作包对应的角色形象已切换，请重新生成动作包")
    frozen = await _process_thread(read_portrait_bytes, context.identity_reference_path)
    current = await _process_thread(read_portrait_bytes, avatar.seed_fullbody_url)
    if frozen is None or current is None:
        raise VideoPackStateError("全身形象无法读取，暂不能启用该动作包")
    if frozen[0] != current[0]:
        raise VideoPackStateError("动作包使用旧身体资料，请先制作新的动作包")


def _same_generation_lineage(kept: CompanionActionPack, other: CompanionActionPack) -> bool:
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


async def _copy_job_to_pack(job: CompanionAction, pack: CompanionActionPack) -> CompanionAction:
    clone = CompanionAction(
        user_id=job.user_id,
        pack_id=pack.id,
        outfit_id=job.outfit_id,
        key=job.key,
        media_type=job.media_type,
        name=job.name,
        system_slot=job.system_slot,
        kind=job.kind,
        motion_description=job.motion_description,
        use_when=job.use_when,
        avoid_when=job.avoid_when,
        enabled=job.enabled,
        metadata_revision=job.metadata_revision,
        status=job.status,
        stage=job.stage,
        provider=job.provider,
        model=job.model,
        provider_task_id=job.provider_task_id,
        generation_state_json=job.generation_state_json,
        pose_generation_state_json=job.pose_generation_state_json,
        reference_hash=job.reference_hash,
        script_json=job.script_json,
        artifact_path=job.artifact_path,
        pose_path=job.pose_path,
        result_json=job.result_json,
        accepted_asset_json=job.accepted_asset_json,
        peek_geometry_json=job.peek_geometry_json,
        content_rect_json=job.content_rect_json,
        source_design_json=job.source_design_json,
        error=job.error,
        media_path=job.media_path,
        media_hash=job.media_hash,
        target_duration_seconds=job.target_duration_seconds,
        actual_duration_ms=job.actual_duration_ms,
        frames=job.frames,
        loopable=job.loopable,
        cover_path=job.cover_path,
        hitmask_path=job.hitmask_path,
        hitmask_grid_w=job.hitmask_grid_w,
        hitmask_grid_h=job.hitmask_grid_h,
        hitmask_fps=job.hitmask_fps,
    )
    if job.status == "review":
        # 复核 publication 绑定原 action_id，跨包继承不能复制出无复核入口的候选。
        material = accepted_action_asset(job)
        clear_action_attempt(clone)
        if material is not None:
            for field, value in material.model_dump().items():
                setattr(clone, field, value)
            if material.media_type == "image":
                clone.kind = clone.target_duration_seconds = clone.actual_duration_ms = clone.frames = (
                    clone.loopable
                ) = clone.hitmask_fps = None
            clone.status, clone.stage, clone.error = "succeeded", "publish", None
        else:
            clone.status, clone.stage = "failed", "design"
            clone.error = "待复核候选未迁入此动作包，请在此版本重新制作"
    copied: dict[str, str] = {}
    try:
        # 克隆须改写全部资产字段（与引用扫描同清单），另加设计来源文本。
        for name in (*ACTION_ASSET_FIELDS, "source_design_json"):
            setattr(clone, name, await _copy_pack_value(getattr(clone, name), pack, copied))
    except BaseException:
        for path in copied.values():
            unlink_companion_asset(path)
        raise
    return clone


async def _carry_incomplete_jobs(
    db: AsyncSession,
    kept: CompanionActionPack,
    targets: list[CompanionActionPack],
    jobs_by_pack: dict[int, list[CompanionAction]],
) -> None:
    """清理前把将删包上的有效记录迁到 kept：不可续跑失败记录与 kept 缺失的成功结果；可续跑任务与血缘不一致任务不迁入。"""
    rows = [job for pack in targets if _same_generation_lineage(kept, pack) for job in jobs_by_pack.get(pack.id, [])]
    if not rows:
        return
    kept_jobs = {job.key: job for job in jobs_by_pack.get(kept.id, [])}
    cloned = False
    for job in rows:
        if job.stage == "merged":
            continue
        if job.reference_hash and kept.reference_hash and job.reference_hash != kept.reference_hash:
            continue
        if _can_resume_job(job):
            continue
        if job.key in kept_jobs:
            continue
        clone = await _copy_job_to_pack(job, kept)
        db.add(clone)
        kept_jobs[job.key] = clone
        cloned = True
    if cloned:
        # autoflush=False：先 flush 让迁入行进入资产引用查询，避免源素材被回收。
        await db.flush()


def _has_pending_work(pack_id: int, jobs: Sequence[CompanionAction]) -> bool:
    """包上有在途或已排队的动作制作；此时删除任务行会让任务之后落盘的素材无人引用。"""
    return pack_id in _GEN_INFLIGHT or any(job.status in ("queued", "processing") for job in jobs)


async def _retire_superseded_locked(
    db: AsyncSession,
    kept: CompanionActionPack,
) -> tuple[set[str], list[RemovedActionVideoTask]]:
    """kept 激活后删除同外观其余历史包。构建中的不动；同血缘包上不可续跑失败记录先迁到 kept；仍有可续跑任务的包保留作续跑入口；有在途或排队制作的包留待制作收尾后退役。"""
    targets = [
        pack
        for pack in (
            await db.execute(
                select(CompanionActionPack).where(
                    CompanionActionPack.user_id == kept.user_id,
                    CompanionActionPack.outfit_id == kept.outfit_id,
                ),
            )
        ).scalars()
        if pack.pack_version < kept.pack_version and pack.status != "processing"
    ]
    if not targets:
        return set(), []
    jobs_by_pack = await _jobs_by_pack(db, CompanionAction.pack_id.in_([kept.id, *(pack.id for pack in targets)]))
    succeeded = {job.key for job in jobs_by_pack.get(kept.id, []) if accepted_action_asset(job) is not None}
    deletable = [
        pack
        for pack in targets
        if not _has_pending_work(pack.id, jobs_by_pack.get(pack.id, []))
        and not (
            _same_generation_lineage(kept, pack)
            and any(_can_resume_job(job) and job.key not in succeeded for job in jobs_by_pack.get(pack.id, []))
        )
    ]
    if not deletable:
        return set(), []
    await _carry_incomplete_jobs(db, kept, deletable, jobs_by_pack)
    return await _remove_packs(db, kept.user_id, deletable)


async def _retire_after_generation(pack_id: int, user_id: int) -> None:
    """同外观已有更新的激活包时，回收该包及其他旧版本；制作期间被跳过的退役在此补做。"""
    async with get_avatar_job_lock(user_id), SESSION_LOCAL() as db:
        pack = await db.get(CompanionActionPack, pack_id)
        if pack is None:
            return
        kept = await db.scalar(
            select(CompanionActionPack)
            .where(
                CompanionActionPack.user_id == user_id,
                CompanionActionPack.outfit_id == pack.outfit_id,
                CompanionActionPack.active.is_(True),
                CompanionActionPack.pack_version > pack.pack_version,
            )
            .limit(1),
        )
        if kept is None:
            return
        _, removed = await _retire_superseded_locked(db, kept)
        await db.commit()
        spawn_removed_action_task_cancellation(user_id, removed)


async def delete_pack(db: AsyncSession, user_id: int, pack_id: int) -> None:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError("找不到动作包")
        jobs = (await _jobs_by_pack(db, CompanionAction.pack_id == pack.id)).get(pack.id, [])
        if pack.active or pack.status == "processing" or _has_pending_work(pack.id, jobs):
            raise VideoPackStateError("使用中或制作中的动作包不能删除")
        _, removed = await _remove_packs(db, user_id, [pack])
        await db.commit()
        spawn_removed_action_task_cancellation(user_id, removed)


async def _get_pack(db: AsyncSession, user_id: int, pack_id: int) -> CompanionActionPack | None:
    return (
        await db.execute(
            select(CompanionActionPack).where(
                CompanionActionPack.id == pack_id,
                CompanionActionPack.user_id == user_id,
            ),
        )
    ).scalar_one_or_none()


def _pack_response(pack: CompanionActionPack, jobs: Sequence[CompanionAction]) -> VideoPackResponse:
    """列表与单包共用响应装配；资源 URL 仅在出口签名。"""
    context = _load_generation_context(pack)
    actions = []
    for job in sorted(jobs, key=lambda job: _action_order(job.key)):
        script = parse_action_script_entry(job.script_json) if job.script_json else None
        material = accepted_action_asset(job)
        preview_path = job.media_path or (material.media_path if material else "")
        if not preview_path and job.generation_state_json:
            try:
                candidate = MediaChainState.model_validate_json(job.generation_state_json).best()
                if candidate is not None and candidate.result_json:
                    preview_path = candidate.path
            except ValueError:
                logger.warning("action preview progress invalid", extra={"action_id": job.id})
        preview_type = job.media_type if job.media_path or material is None else material.media_type
        response_model = VideoActionImageResponse if preview_type == "image" else VideoActionVideoResponse
        video_values = (
            {"motion_prompt": script.motion_prompt if isinstance(script, VideoActionScriptEntry) else ""}
            if preview_type == "video"
            else {}
        )
        actions.append(
            response_model(
                action=job.key,
                name=job.name,
                status=job.status,
                stage=job.stage,
                error=job.error,
                media_url=signed_companion_asset_url(preview_path) if preview_path else None,
                feedback=context.action_feedback.get(job.key, "") if context else "",
                **video_values,
                peek_geometry=PeekGeometry.from_stored_json(job.peek_geometry_json),
                content_rect=parse_content_rect(job.content_rect_json),
            ),
        )
    return VideoPackResponse(
        id=pack.id,
        outfit_id=pack.outfit_id,
        pack_version=pack.pack_version,
        status=pack.status,
        active=pack.active,
        identity_review=pack.identity_review,
        identity_review_reason=pack.identity_review_reason,
        content_hash=pack.content_hash or None,
        manifest_url=signed_companion_asset_url(pack.manifest_path) if pack.status == "ready" else None,
        error=pack.error or None,
        can_regenerate=bool(pack.reference_path) and pack.status in ("ready", "failed"),
        can_retry=(
            bool(pack.reference_path)
            and pack.status == "failed"
            and context is not None
            and context.reference_alignment != "running"
            and (any(_can_resume_job(job) for job in jobs) or _can_publish_jobs(jobs, context.must_actions))
        ),
        actions=actions,
    )


async def load_pack_response(db: AsyncSession, pack: CompanionActionPack) -> VideoPackResponse:
    jobs = (
        await db.scalars(
            select(CompanionAction).where(
                CompanionAction.user_id == pack.user_id,
                CompanionAction.pack_id == pack.id,
            ),
        )
    ).all()
    return _pack_response(pack, jobs)


async def list_pack_responses(db: AsyncSession, user_id: int) -> list[VideoPackResponse]:
    """用户的动作包列表（按创建时间倒序）；manifest URL 现签。"""
    packs = (
        (
            await db.execute(
                select(CompanionActionPack)
                .where(CompanionActionPack.user_id == user_id)
                .order_by(CompanionActionPack.created_at.desc()),
            )
        )
        .scalars()
        .all()
    )
    if not packs:
        return []
    jobs_by_pack = await _jobs_by_pack(db, CompanionAction.user_id == user_id)
    return [_pack_response(pack, jobs_by_pack.get(pack.id, [])) for pack in packs]
