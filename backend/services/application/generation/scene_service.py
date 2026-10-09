"""场景资产生成、分析与启用；供应商等待不持有数据库会话。"""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from components import (
    SCENE_DOWNLOAD_MAX_BYTES,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    is_user_in_maintenance,
    parse_llm_json,
    resolve_language,
    track_user_task,
    utc_now,
)
from modules.auth import User
from modules.companion import (
    CompanionScene,
    Persona,
    SceneDescriptionRequest,
    SceneGenerationAttempt,
    SceneImageDimensions,
    SceneImageSize,
    SceneOrigin,
    ScenePolicy,
    SceneSource,
    SceneStatus,
)
from modules.settings import get_user_setting
from modules.ws import emit_ws_event
from prompts.generation import SCENE_DESCRIBE_SYSTEM
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.assets import cleanup_user_assets, enqueue_asset_cleanup
from services.domains.companion import (
    get_pending_scene_task,
    get_scene,
    load_persona_definition,
)
from services.infrastructure.assets import asset_store, build_data_uri, validate_image_bytes
from services.infrastructure.llm import ServiceType, resolve, vision_chat

from .character_images import ImageChainState, ImageProgressWriter, generate_scene_images
from .image_generation import ImageGenerationError, ImageReviewUnavailableError
from .paid_work import GenerationWorkPaused, require_new_generation_call
from .scene_prompt import build_scene_prompt
from .scene_wallpaper import SCENE_IMAGE_SIZE, WallpaperAsset, prepare_scene_wallpaper, scene_source_extension

logger = get_logger(__name__)
_SCENE_LOCKS: dict[int, asyncio.Lock] = {}
_INFLIGHT_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}
_QUEUED_TASKS: dict[tuple[int, int], tuple[str | None]] = {}
_AUTONOMOUS_ORIGINS = frozenset((SceneOrigin.LLM.value, SceneOrigin.NIGHTLY.value))


class SceneRegenerationState(BaseModel):
    task_id: str
    prompt: str
    target_size: SceneImageSize
    image_chain: ImageChainState = Field(default_factory=ImageChainState)
    wallpaper_path: str = ""
    source_size: SceneImageDimensions | None = None


def scene_generation_wait_seconds(scene: CompanionScene) -> float:
    state = (
        ImageChainState.model_validate_json(scene.generation_state_json)
        if scene.generation_state_json
        else ImageChainState()
    )
    return max(
        900,
        sum(
            max(
                SETTINGS.llm_request_timeout_seconds,
                resolve(ServiceType.image_gen, provider.provider).max_job_wait_seconds or 0,
            )
            + SETTINGS.llm_request_timeout_seconds
            + 120 * max(1, SETTINGS.scene_store_max_attempts)
            for provider in state.providers
        )
        + SETTINGS.llm_request_timeout_seconds
        + 90,
    )


class SceneError(RuntimeError):
    """可展示的场景错误。"""


class SceneNotFoundError(SceneError):
    pass


class SceneStateError(SceneError):
    pass


class SceneLockedError(SceneError):
    pass


class SceneQuotaExceededError(SceneError):
    pass


def _scene_lock(user_id: int) -> asyncio.Lock:
    return _SCENE_LOCKS.setdefault(user_id, asyncio.Lock())


async def _persona(db: AsyncSession, user_id: int) -> Persona:
    persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    if persona is None or not persona.is_complete:
        raise SceneStateError("请先完成角色设定")
    return persona


def _event(db: AsyncSession, persona: Persona, event_type: str, scene_id: int | None = None) -> None:
    persona.scene_state_version += 1
    emit_ws_event(
        db,
        user_id=persona.user_id,
        event_type=event_type,
        payload={
            "scene_id": scene_id,
            "version": persona.scene_state_version,
            "switch_version": persona.scene_switch_version,
        },
    )


async def _check_policy(db: AsyncSession, persona: Persona, origin: str) -> None:
    if origin not in _AUTONOMOUS_ORIGINS:
        return
    if persona.scene_policy == ScenePolicy.LOCKED.value:
        raise SceneLockedError("场景已锁定，自主新增与切换已暂停")
    # 打扰档位只限制打扰用户的主动行为；场景变化不直接打扰用户，对话中的自主变化只受锁定限制，夜间另按总控。
    if origin == SceneOrigin.NIGHTLY.value:
        user = await db.get(User, persona.user_id)
        if user is None or not user.nightly_activity_enabled:
            raise SceneLockedError("夜间自主活动已关闭")


async def set_scene_policy(db: AsyncSession, user_id: int, policy: str) -> ScenePolicy:
    async with _scene_lock(user_id):
        persona = await _persona(db, user_id)
        persona.scene_policy = ScenePolicy(policy).value
        # 即使重新选择相同政策，也撤销尚未兑现的切换意图。
        persona.scene_switch_version += 1
        _event(db, persona, "companion.scene.updated")
        await db.commit()
    return ScenePolicy(policy)


def _validate_ready(user_id: int, row: CompanionScene) -> None:
    if (
        row.status != SceneStatus.READY.value
        or not row.media_path
        or not row.title.strip()
        or not row.description.strip()
    ):
        raise SceneStateError("场景图片、标题与描述齐全后才能启用")
    parsed_path = asset_store.parse_companion_asset_path(row.media_path)
    if not parsed_path or parsed_path[0] != user_id or asset_store.resolve_companion_asset_path(*parsed_path) is None:
        raise SceneStateError("场景图片缺失，请重新创建或上传")


async def activate_scene(
    db: AsyncSession,
    user_id: int,
    scene_id: int,
    *,
    origin: str = SceneOrigin.USER_REQUEST.value,
) -> CompanionScene:
    async with _scene_lock(user_id):
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        persona = await _persona(db, user_id)
        if persona.active_scene_id == scene_id:
            # 用户再次选择当前环境仍取消旧的自动切换意图，但不产生一次环境变化。
            if origin == SceneOrigin.USER_REQUEST.value:
                persona.scene_switch_version += 1
                _event(db, persona, "companion.scene.updated", scene_id)
                await db.commit()
            return row
        await _check_policy(db, persona, origin)
        _validate_ready(user_id, row)
        persona.scene_switch_version += 1
        persona.active_scene_id = scene_id
        row.activated_at = utc_now()
        _event(db, persona, "companion.scene.activated", scene_id)
        await db.commit()
        return row


async def _consume_llm_quota(db: AsyncSession, user_id: int) -> None:
    limit = int(SETTINGS.scene_llm_create_per_24h)
    if limit <= 0:
        return
    count = await db.scalar(
        select(func.count())
        .select_from(SceneGenerationAttempt)
        .where(
            SceneGenerationAttempt.user_id == user_id,
            SceneGenerationAttempt.submitted_at >= utc_now() - timedelta(days=1),
        ),
    )
    if int(count or 0) >= limit:
        raise SceneQuotaExceededError("今天的自主场景新增额度已用完，可以复用已有场景")


async def _new_scene(
    user_id: int,
    *,
    origin: str,
    notes: str,
    source: str,
    auto_activate: bool,
    reference_image: str | None = None,
) -> CompanionScene:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        persona = await _persona(db, user_id)
        await _check_policy(db, persona, origin)
        if await get_pending_scene_task(db, user_id) is not None:
            if auto_activate:
                persona.scene_switch_version += 1
                _event(db, persona, "companion.scene.updated")
                await db.commit()
                raise SceneStateError("已有场景正在准备，之前的自动切换已撤销；请等待完成或取消任务后重试")
            raise SceneStateError("已有场景正在准备，请等待完成或取消该任务")
        if origin == SceneOrigin.ONBOARDING.value and await db.scalar(
            select(CompanionScene.id).where(CompanionScene.user_id == user_id).limit(1),
        ):
            raise SceneStateError("初始场景已准备，不重复创建")
        if origin == SceneOrigin.LLM.value:
            await _consume_llm_quota(db, user_id)
        if auto_activate:
            persona.scene_switch_version += 1
        row = CompanionScene(
            user_id=user_id,
            status=SceneStatus.PENDING.value,
            stage="waiting_upload" if source == SceneSource.USER_UPLOAD.value else "prepare",
            origin=origin,
            source=source,
            requirements=notes,
            prompt=build_scene_prompt(
                notes=notes,
                has_reference_image=bool(reference_image),
            ),
            target_size_json=SCENE_IMAGE_SIZE.model_dump_json(),
            reference_image=reference_image or "",
            auto_activate=auto_activate,
            switch_version=persona.scene_switch_version,
        )
        db.add(row)
        await db.flush()
        _event(db, persona, "companion.scene.updated", row.id)
        await db.commit()
        return row


async def schedule_scene_generation(
    user_id: int,
    *,
    origin: str,
    notes: str | None = None,
    reference_image: bytes | None = None,
    auto_activate: bool = False,
) -> CompanionScene:
    row = await _new_scene(
        user_id,
        origin=origin,
        notes=notes or "",
        source=SceneSource.GENERATED.value,
        auto_activate=auto_activate,
        reference_image=await _prepare_reference_image(reference_image) if reference_image is not None else None,
    )
    _launch_task(row.id, user_id)
    return row


async def schedule_scene_prompt(
    user_id: int,
    *,
    notes: str | None = None,
) -> CompanionScene:
    return await _new_scene(
        user_id,
        origin=SceneOrigin.USER_REQUEST.value,
        notes=notes or "",
        source=SceneSource.USER_UPLOAD.value,
        auto_activate=False,
    )


async def regenerate_scene(user_id: int, scene_id: int) -> CompanionScene:
    cleanup_paths: set[str] = set()
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        if row.status != SceneStatus.READY.value or not row.media_path:
            raise SceneStateError("只有已保存图片的场景才能重新生成")
        if not row.title.strip() or not row.description.strip():
            raise SceneStateError("请先补全场景标题和描述")
        if await get_pending_scene_task(db, user_id) is not None:
            raise SceneStateError("已有场景任务正在准备，请等待完成或取消后重试")
        if row.regeneration_state_json:
            previous = SceneRegenerationState.model_validate_json(row.regeneration_state_json)
            cleanup_paths = previous.image_chain.stored_paths()
        task_id = str(uuid4())
        state = SceneRegenerationState(
            task_id=task_id,
            prompt=build_scene_prompt(notes=row.description),
            target_size=SCENE_IMAGE_SIZE,
        )
        row.regeneration_status = "pending"
        row.regeneration_stage = "prepare"
        row.regeneration_error = None
        row.regeneration_task_id = task_id
        row.regeneration_state_json = state.model_dump_json()
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
    await cleanup_user_assets(user_id)
    _launch_task(scene_id, user_id, regeneration_task_id=task_id)
    return row


async def adopt_scene(user_id: int, scene_id: int | None, *, data: bytes) -> CompanionScene:
    try:
        await asyncio.to_thread(validate_image_bytes, data)
    except Exception as exc:
        raise SceneError("图片无法读取，请换一张有效的 PNG / JPEG / WebP / GIF 图片") from exc
    if scene_id is None:
        row = await schedule_scene_prompt(user_id)
        scene_id = row.id
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        if row.status != "pending" or row.stage != "waiting_upload":
            raise SceneStateError("该场景不在等待上传状态")
        target = SceneImageSize.model_validate_json(row.target_size_json) if row.target_size_json else SCENE_IMAGE_SIZE
        row.stage = "store"
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()
    try:
        saved = await _save_image(user_id, scene_id, data, target=target)
    except Exception:
        await _mark_failed(user_id, scene_id, "图片保存失败，请重新上传")
        raise
    _launch_task(scene_id, user_id)
    if saved is None:
        raise SceneNotFoundError("找不到对应场景")
    return saved


async def discard_scene(user_id: int, scene_id: int) -> CompanionScene:
    cleanup_paths: set[str] = set()
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        persona = await _persona(db, user_id)
        if row.regeneration_status == "pending":
            if row.regeneration_state_json:
                state = SceneRegenerationState.model_validate_json(row.regeneration_state_json)
                cleanup_paths = state.image_chain.stored_paths()
            row.regeneration_status = "cancelled"
            row.regeneration_stage = "cancelled"
            row.regeneration_error = None
            row.regeneration_state_json = None
        elif row.status == "pending":
            row.status = SceneStatus.CANCELLED.value
            row.auto_activate = False
            if row.generation_state_json:
                cleanup_paths = ImageChainState.model_validate_json(row.generation_state_json).stored_paths()
                row.generation_state_json = None
            if row.switch_version == persona.scene_switch_version:
                persona.scene_switch_version += 1
        else:
            raise SceneStateError("该场景没有待取消的任务")
        _event(db, persona, "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
        task = _INFLIGHT_TASKS.get((user_id, scene_id))
        _QUEUED_TASKS.pop((user_id, scene_id), None)
        if task and not task.done():
            task.cancel()
    if task:
        await asyncio.gather(task, return_exceptions=True)
    await cleanup_user_assets(user_id)
    return row


async def delete_scene(user_id: int, scene_id: int) -> None:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        persona = await _persona(db, user_id)
        if persona.active_scene_id == scene_id:
            raise SceneStateError("当前场景不能删除，请先启用其他场景")
        if row.status == "pending":
            raise SceneStateError("请先取消场景任务")
        if row.regeneration_status == "pending":
            raise SceneStateError("请先取消图片重新生成任务")
        paths = {row.media_path, row.upload_source_path} - {""}
        if row.generation_state_json:
            paths |= ImageChainState.model_validate_json(row.generation_state_json).stored_paths()
        if row.regeneration_state_json:
            paths |= SceneRegenerationState.model_validate_json(row.regeneration_state_json).image_chain.stored_paths()
        await db.delete(row)
        _event(db, persona, "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, paths)
        await db.commit()
    await cleanup_user_assets(user_id)


async def edit_scene_description(user_id: int, scene_id: int, description: SceneDescriptionRequest) -> CompanionScene:
    cleanup_paths: set[str] = set()
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        if row.regeneration_status == "pending":
            raise SceneStateError("图片重新生成时不能编辑场景信息")
        if not row.media_path:
            raise SceneStateError("图片尚未保存")
        if row.regeneration_state_json:
            cleanup_paths = SceneRegenerationState.model_validate_json(
                row.regeneration_state_json,
            ).image_chain.stored_paths()
            row.regeneration_state_json = None
            row.regeneration_status = None
            row.regeneration_stage = None
            row.regeneration_task_id = None
            row.regeneration_error = None
        # 人工补全不兑现旧的自动切换；用户明确选择启用。
        row.auto_activate = False
        row.title = description.title
        row.description = description.description
        row.status = SceneStatus.READY.value
        row.stage = "complete"
        row.ready_at = row.ready_at or utc_now()
        row.error = None
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
    await cleanup_user_assets(user_id)
    return row


async def retry_scene_description(user_id: int, scene_id: int) -> CompanionScene:
    regeneration_task_id: str | None = None
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        if await get_pending_scene_task(db, user_id) is not None:
            raise SceneStateError("已有场景正在准备")
        if (
            row.regeneration_status == "failed"
            and row.regeneration_state_json
            and row.regeneration_stage in {"analyze", "review_failed"}
        ):
            frozen = SceneRegenerationState.model_validate_json(row.regeneration_state_json)
            regeneration_task_id = frozen.task_id
            row.regeneration_status = "pending"
            row.regeneration_stage = "analyze" if frozen.wallpaper_path else "evaluating"
            row.regeneration_error = None
        elif row.media_path and row.status in {"description_failed", "cancelled"}:
            row.status = "pending"
            row.stage = "analyze"
            row.error = None
        elif row.status == "failed" and row.stage == "review_failed" and row.generation_state_json:
            row.status = "pending"
            row.stage = "evaluating"
            row.error = None
        else:
            raise SceneStateError("该场景无需重试分析")
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()
    _launch_task(scene_id, user_id, regeneration_task_id=regeneration_task_id)
    return row


async def _save_image(user_id: int, scene_id: int, data: bytes, *, target: SceneImageSize) -> CompanionScene | None:
    """保留上传源文件并派生窗口背景；取消时回收未交接文件。"""
    ext = await asyncio.to_thread(scene_source_extension, data)
    source_path = await asset_store.save_companion_asset_async(
        data,
        user_id=user_id,
        label="scene_source",
        ext=ext,
        directory=asset_store.scene_asset_directory(scene_id),
    )
    generation_id = uuid4().hex
    wallpaper_path = asset_store.scene_wallpaper_asset_path(
        user_id,
        generation_id,
        directory=asset_store.scene_asset_directory(scene_id),
    )
    committed = False
    try:
        wallpaper = await prepare_scene_wallpaper(
            user_id,
            source_path,
            generation_id=generation_id,
            storage_directory=asset_store.scene_asset_directory(scene_id),
            target=target,
        )
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            row = await get_scene(db, user_id, scene_id)
            if row is None or row.status != "pending" or row.stage != "store":
                return row
            row.media_path = wallpaper.path
            row.upload_source_path = source_path
            row.target_size_json = target.model_dump_json()
            row.source_size_json = wallpaper.source_size.model_dump_json()
            row.image_size_json = wallpaper.image_size.model_dump_json()
            row.stage = "analyze"
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
            await db.commit()
            committed = True
            return row
    finally:
        if not committed:
            await _reclaim_assets(user_id, {source_path, wallpaper_path})


async def _scene_image_uri(user_id: int, path: str) -> str:
    parsed_path = asset_store.parse_companion_asset_path(path)
    local = (
        asset_store.resolve_companion_asset_path(*parsed_path) if parsed_path and parsed_path[0] == user_id else None
    )
    if local is None:
        raise SceneStateError("场景图片无法读取")
    data, mime = await asyncio.to_thread(validate_image_bytes, await asyncio.to_thread(local[0].read_bytes))
    return await asyncio.to_thread(build_data_uri, data, mime)


async def _describe_image(user_id: int, path: str) -> SceneDescriptionRequest:
    async with SESSION_LOCAL() as db:
        language = resolve_language(await get_user_setting(db, user_id, "language"))
    data_uri = await _scene_image_uri(user_id, path)
    raw = await vision_chat(
        user_id,
        SCENE_DESCRIBE_SYSTEM,
        json.dumps({"output_language": language}),
        reference_images=(data_uri,),
        before_submit=lambda: _scene_paid_boundary(user_id),
    )
    return SceneDescriptionRequest.model_validate(parse_llm_json(raw))


async def _analyze(user_id: int, scene_id: int) -> None:
    cleanup_paths: set[str] = set()
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending" or not row.media_path:
            return
        path = row.media_path
    description = await _describe_image(user_id, path)
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending" or row.media_path != path:
            return
        row.title = description.title
        row.description = description.description
        row.status = SceneStatus.READY.value
        row.stage = "complete"
        row.ready_at = utc_now()
        row.error = None
        if row.generation_state_json:
            cleanup_paths = ImageChainState.model_validate_json(row.generation_state_json).stored_paths()
            row.generation_state_json = None
        row.reference_image = ""
        persona = await _persona(db, user_id)
        _event(db, persona, "companion.scene.updated", scene_id)
        can_activate = row.auto_activate and row.switch_version == persona.scene_switch_version
        if can_activate:
            try:
                await _check_policy(db, persona, row.origin)
                _validate_ready(user_id, row)
            except SceneError:
                can_activate = False
        if can_activate:
            persona.active_scene_id = row.id
            row.activated_at = utc_now()
            _event(db, persona, "companion.scene.activated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
    await cleanup_user_assets(user_id)


async def _reclaim_assets(user_id: int, paths: set[str]) -> None:
    """未在业务事务内登记的零散路径：登记待办后立即回收。"""
    if not paths:
        return
    async with SESSION_LOCAL() as db:
        await enqueue_asset_cleanup(db, user_id, paths)
        await db.commit()
    await cleanup_user_assets(user_id)


async def _generate_wallpaper(
    user_id: int,
    prompt: str,
    target: SceneImageSize,
    state: ImageChainState,
    save_progress: ImageProgressWriter,
    *,
    reference_image: str | None = None,
    scene_id: int,
) -> WallpaperAsset:
    paths = await generate_scene_images(
        prompt,
        user_id=user_id,
        storage_directory=asset_store.scene_asset_directory(scene_id),
        target_width=target.width,
        target_height=target.height,
        reference_image=reference_image,
        state=state,
        save_progress=save_progress,
        store_attempts=SETTINGS.scene_store_max_attempts,
        max_image_bytes=SCENE_DOWNLOAD_MAX_BYTES,
        before_submit=lambda: _scene_paid_boundary(user_id),
    )
    best = state.best()
    if best is None:
        raise SceneStateError("场景图片未通过伙伴重复出镜检查")
    path = asset_store.scene_wallpaper_asset_path(
        user_id,
        state.generation_id,
        directory=asset_store.scene_asset_directory(scene_id),
    )
    if path not in best.artifacts:
        best.artifacts.append(path)
    # 先登记确定的成品路径，取消或重启时仍可保护或回收裁切结果。
    await save_progress(state)
    return await prepare_scene_wallpaper(
        user_id,
        paths[0],
        generation_id=state.generation_id,
        target=target,
        storage_directory=asset_store.scene_asset_directory(scene_id),
    )


async def _run_scene_regeneration(user_id: int, scene_id: int, task_id: str) -> None:
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.regeneration_status != "pending" or row.regeneration_task_id != task_id:
            return
        if not row.regeneration_state_json:
            raise SceneStateError("重新生成任务资料缺失")
        frozen = SceneRegenerationState.model_validate_json(row.regeneration_state_json)
        if frozen.task_id != task_id:
            raise SceneStateError("重新生成任务已失效")
        state = frozen.image_chain

    async def save_progress(progress: ImageChainState) -> None:
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            fresh = await get_scene(db, user_id, scene_id)
            if (
                fresh is None
                or fresh.regeneration_status != "pending"
                or fresh.regeneration_task_id != task_id
                or not fresh.regeneration_state_json
            ):
                raise asyncio.CancelledError
            current = SceneRegenerationState.model_validate_json(fresh.regeneration_state_json)
            if progress.phase == "submitting" and (
                fresh.regeneration_stage != "submitting" or current.image_chain.active_index != progress.active_index
            ):
                fresh.attempt_count += 1
            frozen.image_chain = progress
            fresh.regeneration_state_json = frozen.model_dump_json()
            fresh.regeneration_stage = "analyze" if frozen.wallpaper_path else progress.phase
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
            await db.commit()

    old_path = ""
    try:
        if not frozen.wallpaper_path:
            wallpaper = await _generate_wallpaper(
                user_id,
                frozen.prompt,
                frozen.target_size,
                state,
                save_progress,
                scene_id=scene_id,
            )
            frozen.wallpaper_path = wallpaper.path
            frozen.source_size = wallpaper.source_size
            await save_progress(state)
        description = await _describe_image(user_id, frozen.wallpaper_path)
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            row = await get_scene(db, user_id, scene_id)
            if row is None or row.regeneration_status != "pending" or row.regeneration_task_id != task_id:
                return
            old_path = row.media_path
            row.media_path = frozen.wallpaper_path
            row.description = description.description
            row.prompt = frozen.prompt
            row.source = SceneSource.GENERATED.value
            row.reference_image = ""
            row.target_size_json = frozen.target_size.model_dump_json()
            row.source_size_json = frozen.source_size.model_dump_json() if frozen.source_size else None
            row.image_size_json = frozen.target_size.model_dump_json()
            row.generation_state_json = None
            row.regeneration_status = "ready"
            row.regeneration_stage = "complete"
            row.regeneration_error = None
            row.regeneration_state_json = None
            await enqueue_asset_cleanup(db, user_id, state.stored_paths() | ({old_path} if old_path else set()))
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
            await db.commit()
    finally:
        async with SESSION_LOCAL() as db:
            retained = await db.scalar(
                select(CompanionScene.id).where(
                    CompanionScene.user_id == user_id,
                    CompanionScene.id == scene_id,
                    CompanionScene.regeneration_task_id == task_id,
                    CompanionScene.regeneration_state_json.is_not(None),
                ),
            )
        if retained is None:
            await _reclaim_assets(user_id, state.stored_paths() | ({old_path} if old_path else set()))


async def _mark_regeneration_failed(
    user_id: int,
    scene_id: int,
    task_id: str,
    error: str,
    *,
    review_failed: bool = False,
) -> None:
    cleanup_paths: set[str] = set()
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.regeneration_status != "pending" or row.regeneration_task_id != task_id:
            return
        current = (
            SceneRegenerationState.model_validate_json(row.regeneration_state_json)
            if row.regeneration_state_json
            else None
        )
        retry_analysis = current is not None and (bool(current.wallpaper_path) or review_failed)
        if current and current.image_chain.phase == "submitting":
            error = "生图提交结果未知，未自动重复付费请求；请核对供应商任务后再决定是否重新生成"
        if not retry_analysis:
            cleanup_paths = current.image_chain.stored_paths() if current else set()
            row.regeneration_state_json = None
        row.regeneration_status = "failed"
        row.regeneration_stage = "review_failed" if review_failed else "analyze" if retry_analysis else "failed"
        row.regeneration_error = error
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
    await cleanup_user_assets(user_id)


async def _run_pipeline(scene_id: int, user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending":
            return
        if row.source == SceneSource.USER_UPLOAD.value and not row.media_path:
            return
        state = (
            ImageChainState.model_validate_json(row.generation_state_json)
            if row.generation_state_json
            else ImageChainState()
        )
        needs_image = not row.media_path
        prompt = row.prompt
        reference_image = row.reference_image or None
        if not row.target_size_json:
            raise SceneStateError("场景任务缺少已冻结的生活空间尺寸")
        target = SceneImageSize.model_validate_json(row.target_size_json)

    async def save_progress(progress: ImageChainState) -> None:
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            fresh = await get_scene(db, user_id, scene_id)
            if fresh is None or fresh.status != "pending":
                raise asyncio.CancelledError
            previous = (
                ImageChainState.model_validate_json(fresh.generation_state_json)
                if fresh.generation_state_json
                else None
            )
            if progress.phase == "submitting" and (
                previous is None or previous.phase != "submitting" or previous.active_index != progress.active_index
            ):
                await _check_policy(db, await _persona(db, user_id), fresh.origin)
                if fresh.origin == SceneOrigin.LLM.value:
                    if fresh.attempt_count == 0:
                        await _consume_llm_quota(db, user_id)
                    db.add(SceneGenerationAttempt(user_id=user_id, scene_id=scene_id))
                fresh.attempt_count += 1
            fresh.generation_state_json = progress.model_dump_json()
            fresh.stage = "analyze" if progress.phase == "complete" else progress.phase
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
            await db.commit()

    if needs_image:
        wallpaper = await _generate_wallpaper(
            user_id,
            prompt,
            target,
            state,
            save_progress,
            scene_id=scene_id,
            reference_image=reference_image,
        )
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            fresh = await get_scene(db, user_id, scene_id)
            if fresh is None or fresh.status != "pending":
                await _reclaim_assets(user_id, {wallpaper.path})
                return
            fresh.media_path = wallpaper.path
            fresh.target_size_json = target.model_dump_json()
            fresh.source_size_json = wallpaper.source_size.model_dump_json()
            fresh.image_size_json = wallpaper.image_size.model_dump_json()
            fresh.generation_state_json = state.model_dump_json()
            fresh.stage = "analyze"
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
            await db.commit()
    await _analyze(user_id, scene_id)


async def _mark_failed(user_id: int, scene_id: int, error: str, *, review_failed: bool = False) -> None:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending":
            return
        row.status = SceneStatus.DESCRIPTION_FAILED.value if row.media_path else SceneStatus.FAILED.value
        row.error = error
        if review_failed:
            row.stage = "review_failed"
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()


def _launch_task(scene_id: int, user_id: int, *, regeneration_task_id: str | None = None) -> None:
    existing = _INFLIGHT_TASKS.get((user_id, scene_id))
    if existing is not None and not existing.done():
        _QUEUED_TASKS[user_id, scene_id] = (regeneration_task_id,)
        return
    _QUEUED_TASKS.pop((user_id, scene_id), None)

    async def runner() -> None:
        try:
            if regeneration_task_id:
                await _run_scene_regeneration(user_id, scene_id, regeneration_task_id)
            else:
                await _run_pipeline(scene_id, user_id)
        except GenerationWorkPaused:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("scene pipeline failed", extra={"scene_id": scene_id, "user_id": user_id})
            if regeneration_task_id:
                message = (
                    str(exc)
                    if isinstance(exc, (SceneError, ImageGenerationError))
                    else "场景图片重新生成失败；原图片仍然保留，请稍后重试"
                )
                await _mark_regeneration_failed(
                    user_id,
                    scene_id,
                    regeneration_task_id,
                    message,
                    review_failed=isinstance(exc, ImageReviewUnavailableError),
                )

                return
            async with SESSION_LOCAL() as db:
                row = await get_scene(db, user_id, scene_id)
            if isinstance(exc, SceneError):
                error = str(exc)
            elif row and row.media_path:
                error = "图片已保存，描述分析失败；可重试分析或手动补全"
            elif row and row.stage == "submitting":
                error = "生图结果未确认，未自动重发付费请求；请核对后再创建"
            elif isinstance(exc, ImageGenerationError):
                error = str(exc)
            else:
                error = "场景准备失败，请查看任务状态后重试"
            await _mark_failed(user_id, scene_id, error, review_failed=isinstance(exc, ImageReviewUnavailableError))

    def completed(done: asyncio.Task[None]) -> None:
        queued: tuple[str | None] | None = None
        if _INFLIGHT_TASKS.get((user_id, scene_id)) is done:
            _INFLIGHT_TASKS.pop((user_id, scene_id), None)
            queued = _QUEUED_TASKS.pop((user_id, scene_id), None)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error("scene task interrupted", extra={"scene_id": scene_id, "user_id": user_id}, exc_info=error)
        if queued:
            _launch_task(scene_id, user_id, regeneration_task_id=queued[0])

    task = asyncio.create_task(runner(), name=f"companion.scene.{user_id}.{scene_id}")
    _INFLIGHT_TASKS[user_id, scene_id] = task
    task.add_done_callback(completed)
    # 维护等待生成结果与描述落库，避免中断已提交的付费请求或遗留 pending 行。
    track_user_task(user_id, task, cancel_on_maintenance=False)


async def resume_scene_generation(user_id: int, scene_id: int) -> bool:
    if is_user_in_maintenance(user_id):
        return True
    if (task := _INFLIGHT_TASKS.get((user_id, scene_id))) is not None and not task.done():
        return True
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending":
            return False
        if row.stage == "waiting_upload":
            return True
        resumable = bool(row.media_path or row.generation_state_json or row.stage == "prepare")
    if resumable:
        _launch_task(scene_id, user_id)
    else:
        await _mark_failed(user_id, scene_id, "任务已中断；结果未知的付费请求不会自动重发，请核对后重新创建")
    return resumable


async def resume_scene_jobs() -> None:
    async with SESSION_LOCAL() as db:
        rows = (
            await db.execute(
                select(CompanionScene.user_id, CompanionScene.id).where(CompanionScene.status == "pending"),
            )
        ).all()
    for user_id, scene_id in rows:
        await resume_scene_generation(user_id, scene_id)

    async with SESSION_LOCAL() as db:
        regenerations = (
            await db.execute(
                select(CompanionScene.user_id, CompanionScene.id, CompanionScene.regeneration_task_id).where(
                    CompanionScene.regeneration_status == "pending",
                ),
            )
        ).all()
    for user_id, scene_id, task_id in regenerations:
        if task_id and not is_user_in_maintenance(user_id):
            _launch_task(scene_id, user_id, regeneration_task_id=task_id)


async def _scene_paid_boundary(user_id: int) -> None:
    require_new_generation_call(user_id)


async def resume_user_scene_jobs(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        scenes = (
            await db.scalars(
                select(CompanionScene).where(
                    CompanionScene.user_id == user_id,
                    (CompanionScene.status == "pending") | (CompanionScene.regeneration_status == "pending"),
                ),
            )
        ).all()
    for row in scenes:
        if row.regeneration_status == "pending" and row.regeneration_task_id:
            _launch_task(row.id, user_id, regeneration_task_id=row.regeneration_task_id)
        elif row.status == "pending":
            await resume_scene_generation(user_id, row.id)


_INITIAL_SCENE_DEFAULT_NOTES = "整洁宁静的日常起居空间，光线柔和，桌椅、床铺与少量个人物品摆放合理。"


def _initial_scene_notes(persona: Persona) -> str:
    """初始房间只用性格影响氛围；说话风格、关系与用户资料不是画面信息，关系还可能引出第二个人物。"""
    personality = load_persona_definition(persona).get("personality", "").strip()
    if not personality:
        return _INITIAL_SCENE_DEFAULT_NOTES
    return f"{_INITIAL_SCENE_DEFAULT_NOTES}房间的氛围与摆设可以体现伙伴的性格：{personality[:200]}。"


async def schedule_initial_scene(user_id: int) -> CompanionScene | None:
    async with SESSION_LOCAL() as db:
        persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
        if persona is None or not persona.is_complete:
            return None
        if await db.scalar(select(CompanionScene.id).where(CompanionScene.user_id == user_id).limit(1)):
            return None
        notes = _initial_scene_notes(persona)
    try:
        return await schedule_scene_generation(
            user_id,
            origin=SceneOrigin.ONBOARDING.value,
            notes=notes,
            auto_activate=True,
        )
    except SceneStateError as exc:
        logger.warning("initial scene not scheduled", extra={"user_id": user_id, "error": str(exc)})
        return None


async def drain_scene_jobs() -> None:
    _QUEUED_TASKS.clear()
    tasks = list(_INFLIGHT_TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _INFLIGHT_TASKS.clear()


async def _prepare_reference_image(data: bytes) -> str:
    """校验用户参考图并按实际格式冻结为 data URI。"""
    try:
        data, mime = await asyncio.to_thread(validate_image_bytes, data)
    except Exception as exc:
        raise SceneError("参考图无法读取，请选择有效图片") from exc
    return await asyncio.to_thread(build_data_uri, data, mime)
