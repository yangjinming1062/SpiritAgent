"""场景素材生成与分析；供应商等待不持有数据库会话。"""

import asyncio
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from infrastructure.ai.prompt_engineer import vision_chat
from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.assets.asset_store import build_data_uri, validate_image_bytes
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.async_io import cancel_and_wait, cancel_tasks
from infrastructure.config import SETTINGS
from infrastructure.constants import SCENE_DOWNLOAD_MAX_BYTES
from infrastructure.database import SESSION_LOCAL
from infrastructure.domain_locks import lock_domain
from infrastructure.logger import get_logger
from infrastructure.text import parse_llm_json, resolve_language, utc_now
from pydantic import BaseModel, Field
from runtime.events.emit import emit_ws_event
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import nightly_account
from features.assets.lifecycle import cleanup_user_assets, enqueue_asset_cleanup
from features.companion.persona_service import load_persona_definition
from features.companion.query_contracts import PersonaSnapshot
from features.companion.visual_state import (
    change_scene_policy,
    persona_snapshot,
)
from features.configuration.settings.values import get_user_setting
from features.jobs.service import bind_job_request, requested_job_id
from features.life.schemas import LifeStepRequest
from features.life.use_cases import bind_step_in_session, require_step_submission, update_business_steps_in_session
from features.media.character_images import ImageChainState, ImageProgressWriter, generate_scene_images
from features.media.image_generation import ImageGenerationError, ImageReviewUnavailableError
from features.media.provider_attempts import count_scene_submissions, record_scene_submission
from features.scenes.asset_references import sync_scene_assets
from features.scenes.models import (
    CompanionScene,
    SceneJob,
    SceneOrigin,
    ScenePolicy,
    SceneSource,
    SceneStatus,
)
from features.scenes.prompt_assembly import build_scene_prompt
from features.scenes.prompts import SCENE_DESCRIBE_SYSTEM
from features.scenes.queries import can_retry_scene_generation, get_pending_scene_task, get_scene, scene_view
from features.scenes.query_contracts import SceneSnapshot
from features.scenes.schemas import SceneDescriptionRequest, SceneImageDimensions, SceneImageSize
from features.scenes.wallpaper import SCENE_IMAGE_SIZE, WallpaperAsset, prepare_scene_wallpaper, scene_source_extension

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
    selection: ProviderSelection | None = None


class SceneError(RuntimeError):
    """可展示的场景错误。"""


class SceneNotFoundError(SceneError):
    pass


class SceneStateError(SceneError):
    pass


class SceneBusyError(SceneStateError):
    pass


class SceneLockedError(SceneError):
    pass


class SceneQuotaExceededError(SceneError):
    pass


def _scene_lock(user_id: int) -> asyncio.Lock:
    return _SCENE_LOCKS.setdefault(user_id, asyncio.Lock())


def _require_complete(persona: PersonaSnapshot | None) -> PersonaSnapshot:
    if persona is None or not persona.is_complete:
        raise SceneStateError("请先完成角色设定")
    return persona


async def _persona(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    return _require_complete(await persona_snapshot(db, user_id))


async def _require_scene(db: AsyncSession, user_id: int, scene_id: int) -> CompanionScene:
    row = await get_scene(db, user_id, scene_id)
    if row is None:
        raise SceneNotFoundError("找不到对应场景")
    if row.initial_job is None:
        raise SceneStateError("场景制作记录缺失")
    return row


def _regeneration_asset_paths(row: CompanionScene) -> set[str]:
    if row.replacement_job is None or not row.replacement_job.checkpoint:
        return set()
    return SceneRegenerationState.model_validate(row.replacement_job.checkpoint).image_chain.stored_paths()


async def _event(db: AsyncSession, user_id: int, event_type: str, scene_id: int | None = None) -> None:
    _require_complete(await persona_snapshot(db, user_id))
    emit_ws_event(
        db,
        user_id=user_id,
        event_type=event_type,
        payload={"scene_id": scene_id},
    )
    if scene_id is not None:
        scene = await get_scene(db, user_id, scene_id)
        if scene is not None:
            await sync_scene_assets(db, scene)
            job = scene.initial_job
            if job is not None and job.state in {"succeeded", "failed", "cancelled", "unknown"}:
                await update_business_steps_in_session(
                    db,
                    user_id,
                    kind="scene.create",
                    business_ref={"job_id": job.id},
                    state="completed" if job.state == "succeeded" else job.state,
                    result_ref={"job_id": job.id, "scene_id": scene.id} if job.state == "succeeded" else {},
                    error=job.error,
                )
        if scene is not None and scene.status == "ready":
            emit_ws_event(db, user_id=user_id, event_type="companion.visual.initial_requested", payload={})


async def _check_policy(db: AsyncSession, persona: PersonaSnapshot, origin: str) -> None:
    if origin not in _AUTONOMOUS_ORIGINS:
        return
    if persona.scene_policy == ScenePolicy.LOCKED.value:
        raise SceneLockedError("场景已锁定，自主新增已暂停")
    # 打扰档位只限制打扰用户的主动行为；场景素材新增不直接打扰用户，对话中的自主新增只受锁定限制，夜间另按总控。
    if origin == SceneOrigin.NIGHTLY.value:
        user = await nightly_account(db, persona.user_id)
        if user is None or not user.nightly_activity_enabled:
            raise SceneLockedError("夜间自主活动已关闭")


async def set_scene_policy(db: AsyncSession, user_id: int, policy: str) -> ScenePolicy:
    async with _scene_lock(user_id):
        persona = await _persona(db, user_id)
        persona = await change_scene_policy(db, user_id, ScenePolicy(policy).value)
        await _event(db, persona.user_id, "companion.scene.updated")
        await db.commit()
    return ScenePolicy(policy)


async def _consume_llm_quota(db: AsyncSession, user_id: int) -> None:
    limit = int(SETTINGS.scene_llm_create_per_24h)
    if limit <= 0:
        return
    count = await count_scene_submissions(db, user_id, utc_now() - timedelta(days=1))
    if int(count or 0) >= limit:
        raise SceneQuotaExceededError("今天的自主场景新增额度已用完，可以复用已有场景")


async def _new_scene(
    user_id: int,
    *,
    origin: str,
    notes: str,
    source: str,
    reference_image: str | None = None,
    life_step: LifeStepRequest | None = None,
) -> CompanionScene:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        fingerprint = hashlib.sha256(notes.encode()).hexdigest()
        if life_step is not None:
            job_id = await requested_job_id(db, user_id, life_step.idempotency_key, fingerprint)
            if job_id is not None:
                existing = await db.scalar(
                    select(CompanionScene).where(
                        CompanionScene.user_id == user_id, CompanionScene.initial_job_id == job_id
                    )
                )
                if existing is None:
                    raise SceneStateError("原制作目标已删除，不能重新提交同一请求")
                return existing
        await require_step_submission(db, user_id, life_step)
        persona = await _persona(db, user_id)
        await _check_policy(db, persona, origin)
        if await get_pending_scene_task(db, user_id) is not None:
            raise SceneBusyError("已有场景正在准备，请等待完成或取消该任务")
        if origin == SceneOrigin.ONBOARDING.value and await db.scalar(
            select(CompanionScene.id).where(CompanionScene.user_id == user_id).limit(1),
        ):
            raise SceneStateError("初始场景已准备，不重复创建")
        if origin == SceneOrigin.LLM.value:
            await _consume_llm_quota(db, user_id)
        row = CompanionScene(
            user_id=user_id,
            status=SceneStatus.PENDING.value,
            origin=origin,
            source=source,
            requirements=notes,
            prompt=build_scene_prompt(
                notes=notes,
                has_reference_image=bool(reference_image),
            ),
            target_size_json=SCENE_IMAGE_SIZE.model_dump(mode="json"),
            reference_image=reference_image or "",
        )
        row.initial_job.stage = "waiting_upload" if source == SceneSource.USER_UPLOAD.value else "prepare"
        db.add(row)
        await db.flush()
        if life_step is not None:
            await bind_job_request(db, user_id, row.initial_job.id, life_step.idempotency_key, fingerprint)
            await bind_step_in_session(db, user_id, life_step, business_ref={"job_id": row.initial_job.id})
        await _event(db, persona.user_id, "companion.scene.updated", row.id)
        await db.commit()
        return row


async def schedule_scene_generation(
    user_id: int,
    *,
    origin: str,
    notes: str | None = None,
    reference_image: bytes | None = None,
    life_step: LifeStepRequest | None = None,
) -> CompanionScene:
    row = await _new_scene(
        user_id,
        origin=origin,
        notes=notes or "",
        source=SceneSource.GENERATED.value,
        reference_image=await _prepare_reference_image(reference_image) if reference_image is not None else None,
        life_step=life_step,
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
    )


async def regenerate_scene(user_id: int, scene_id: int) -> CompanionScene:
    cleanup_paths: set[str] = set()
    regeneration_task_id: str | None = None
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await _require_scene(db, user_id, scene_id)
        if await get_pending_scene_task(db, user_id) is not None:
            raise SceneStateError("已有场景任务正在准备，请等待完成或取消后重试")
        if row.status == SceneStatus.FAILED.value and row.source == SceneSource.GENERATED.value:
            if not can_retry_scene_generation(row):
                raise SceneStateError("该场景不能重试生成，请先核对任务结果或重试图片与描述检查")
            state = ImageChainState.model_validate(row.initial_job.checkpoint) if row.initial_job.checkpoint else None
            # 已返回的图片继续保存或检查；没有可续处理结果时，用原输入重新解析当前模型链。
            if state and (
                state.pending_urls or state.best() or any(not candidate.evaluated for candidate in state.candidates)
            ):
                row.initial_job.stage = state.phase
            else:
                cleanup_paths = state.stored_paths() if state else set()
                row.initial_job.checkpoint = None
                row.initial_job.stage = "prepare"
            row.status = SceneStatus.PENDING.value
            row.initial_job.error = None
            row.origin = SceneOrigin.USER_REQUEST.value
        elif row.status == SceneStatus.READY.value and row.media_path:
            if not row.title.strip() or not row.description.strip():
                raise SceneStateError("请先补全场景标题和描述")
            cleanup_paths = _regeneration_asset_paths(row)
            regeneration_task_id = str(uuid4())
            regeneration = SceneRegenerationState(
                task_id=regeneration_task_id,
                prompt=build_scene_prompt(notes=row.description),
                target_size=SCENE_IMAGE_SIZE,
                selection=ProviderSelection(
                    origin=GenerationOrigin.model_validate(row.generation_origin_json)
                    if row.generation_origin_json
                    else None,
                    mode="next",
                ),
            )
            row.replacement_job = SceneJob(user_id=user_id, scene=row, purpose="replace", token=regeneration_task_id)
            row.replacement_job.availability = "pending"
            row.replacement_job.stage = "prepare"
            row.replacement_job.error = None
            row.replacement_job.checkpoint = regeneration.model_dump(mode="json")
        else:
            raise SceneStateError("只有失败的生成任务或已保存图片的场景才能重新生成")
        await _event(db, user_id, "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
    await cleanup_user_assets(user_id)
    _launch_task(scene_id, user_id, regeneration_task_id=regeneration_task_id)
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
        row = await _require_scene(db, user_id, scene_id)
        if row.status != "pending" or row.initial_job.stage != "waiting_upload":
            raise SceneStateError("该场景不在等待上传状态")
        target = SceneImageSize.model_validate(row.target_size_json) if row.target_size_json else SCENE_IMAGE_SIZE
        row.initial_job.stage = "store"
        await _event(db, user_id, "companion.scene.updated", scene_id)
        await db.commit()
    try:
        saved = await _save_image(user_id, scene_id, data, target=target)
    except (Exception, asyncio.CancelledError):
        await _mark_failed(user_id, scene_id, "图片保存失败，请重新上传")
        raise
    _launch_task(scene_id, user_id)
    if saved is None:
        raise SceneNotFoundError("找不到对应场景")
    return saved


async def discard_scene(user_id: int, scene_id: int) -> CompanionScene:
    cleanup_paths: set[str] = set()
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await _require_scene(db, user_id, scene_id)
        persona = await _persona(db, user_id)
        if row.replacement_job is not None and row.replacement_job.availability == "pending":
            cleanup_paths = _regeneration_asset_paths(row)
            row.replacement_job.availability = "cancelled"
            row.replacement_job.stage = "cancelled"
            row.replacement_job.error = None
            row.replacement_job.checkpoint = None
        elif row.status == "pending":
            row.status = SceneStatus.CANCELLED.value
            if row.initial_job.checkpoint:
                cleanup_paths = ImageChainState.model_validate(row.initial_job.checkpoint).stored_paths()
                row.initial_job.checkpoint = None
        else:
            raise SceneStateError("该场景没有待取消的任务")
        await _event(db, persona.user_id, "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
        task = _INFLIGHT_TASKS.get((user_id, scene_id))
        _QUEUED_TASKS.pop((user_id, scene_id), None)
        cancel_tasks([task])
    await cancel_and_wait([task])
    await cleanup_user_assets(user_id)
    return row


async def delete_scene(user_id: int, scene_id: int) -> None:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        await lock_domain(db, user_id, "scenes")
        row = await _require_scene(db, user_id, scene_id)
        persona = await _persona(db, user_id)
        if row.status == "pending":
            raise SceneStateError("请先取消场景任务")
        if row.replacement_job is not None and row.replacement_job.availability == "pending":
            raise SceneStateError("请先取消图片重新生成任务")
        paths = {row.media_path, row.upload_source_path} - {""}
        if row.initial_job.checkpoint:
            paths |= ImageChainState.model_validate(row.initial_job.checkpoint).stored_paths()
        paths |= _regeneration_asset_paths(row)
        await db.delete(row)
        await _event(db, persona.user_id, "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, paths)
        await db.commit()
    await cleanup_user_assets(user_id)


async def edit_scene_description(user_id: int, scene_id: int, description: SceneDescriptionRequest) -> CompanionScene:
    cleanup_paths: set[str] = set()
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await _require_scene(db, user_id, scene_id)
        if row.replacement_job is not None and row.replacement_job.availability == "pending":
            raise SceneStateError("图片重新生成时不能编辑场景信息")
        if not row.media_path:
            raise SceneStateError("图片尚未保存")
        if row.replacement_job is not None and row.replacement_job.checkpoint:
            cleanup_paths = _regeneration_asset_paths(row)
            row.replacement_job.checkpoint = None
            row.replacement_job = None
        row.title = description.title
        row.description = description.description
        row.status = SceneStatus.READY.value
        row.initial_job.stage = "complete"
        row.ready_at = row.ready_at or utc_now()
        row.initial_job.error = None
        await _event(db, user_id, "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
    await cleanup_user_assets(user_id)
    return row


async def retry_scene_description(user_id: int, scene_id: int) -> CompanionScene:
    regeneration_task_id: str | None = None
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await _require_scene(db, user_id, scene_id)
        if await get_pending_scene_task(db, user_id) is not None:
            raise SceneStateError("已有场景正在准备")
        if (
            row.replacement_job is not None
            and row.replacement_job.availability == "failed"
            and row.replacement_job.checkpoint
            and row.replacement_job.stage in {"analyze", "review_failed"}
        ):
            frozen = SceneRegenerationState.model_validate(row.replacement_job.checkpoint)
            regeneration_task_id = frozen.task_id
            row.replacement_job.availability = "pending"
            row.replacement_job.stage = "analyze" if frozen.wallpaper_path else "evaluating"
            row.replacement_job.error = None
        elif row.media_path and row.status in {"description_failed", "cancelled"}:
            row.status = "pending"
            row.initial_job.stage = "analyze"
            row.initial_job.error = None
        elif row.status == "failed" and row.initial_job.stage == "review_failed" and row.initial_job.checkpoint:
            row.status = "pending"
            row.initial_job.stage = "evaluating"
            row.initial_job.error = None
        else:
            raise SceneStateError("该场景无需重试分析")
        await _event(db, user_id, "companion.scene.updated", scene_id)
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
            if row is None or row.initial_job is None or row.status != "pending" or row.initial_job.stage != "store":
                return row
            row.media_path = wallpaper.path
            row.generation_origin_json = None
            row.upload_source_path = source_path
            row.target_size_json = target.model_dump(mode="json")
            row.source_size_json = wallpaper.source_size.model_dump(mode="json")
            row.image_size_json = wallpaper.image_size.model_dump(mode="json")
            row.initial_job.stage = "analyze"
            await _event(db, user_id, "companion.scene.updated", scene_id)
            await db.commit()
            committed = True
            return row
    finally:
        if not committed:
            await _reclaim_assets(user_id, {source_path, wallpaper_path})


async def _scene_image_uri(user_id: int, path: str) -> str:
    parsed_path = parse_companion_asset_path(path)
    uri = (
        await asyncio.to_thread(asset_store.read_valid_image_data_uri, path)
        if parsed_path and parsed_path[0] == user_id
        else None
    )
    if uri is None:
        raise SceneStateError("场景图片无法读取")
    return uri


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
        task=AiTaskKey.scene,
    )
    return SceneDescriptionRequest.model_validate(parse_llm_json(raw))


async def _analyze(user_id: int, scene_id: int) -> None:
    cleanup_paths: set[str] = set()
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.initial_job is None or row.status != "pending" or not row.media_path:
            return
        path = row.media_path
    description = await _describe_image(user_id, path)
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.initial_job is None or row.status != "pending" or row.media_path != path:
            return
        row.title = description.title
        row.description = description.description
        row.status = SceneStatus.READY.value
        row.initial_job.stage = "complete"
        row.ready_at = utc_now()
        row.initial_job.error = None
        if row.initial_job.checkpoint:
            cleanup_paths = ImageChainState.model_validate(row.initial_job.checkpoint).stored_paths()
            row.initial_job.checkpoint = None
        row.reference_image = ""
        persona = await _persona(db, user_id)
        await _event(db, persona.user_id, "companion.scene.updated", scene_id)
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
    selection: ProviderSelection | None = None,
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
        selection=selection,
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
        if (
            row is None
            or row.replacement_job is None
            or row.replacement_job.availability != "pending"
            or row.replacement_job.token != task_id
        ):
            return
        if row.replacement_job is None or not row.replacement_job.checkpoint:
            raise SceneStateError("重新生成任务资料缺失")
        frozen = SceneRegenerationState.model_validate(row.replacement_job.checkpoint)
        if frozen.task_id != task_id:
            raise SceneStateError("重新生成任务已失效")
        state = frozen.image_chain

    async def save_progress(progress: ImageChainState) -> None:
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            fresh = await get_scene(db, user_id, scene_id)
            if (
                fresh is None
                or fresh.replacement_job is None
                or fresh.replacement_job.availability != "pending"
                or fresh.replacement_job.token != task_id
                or not fresh.replacement_job.checkpoint
            ):
                raise asyncio.CancelledError
            current = SceneRegenerationState.model_validate(fresh.replacement_job.checkpoint)
            if progress.phase == "submitting" and (
                fresh.replacement_job.stage != "submitting" or current.image_chain.active_index != progress.active_index
            ):
                fresh.replacement_job.attempt_count += 1
            frozen.image_chain = progress
            fresh.replacement_job.checkpoint = frozen.model_dump(mode="json")
            fresh.replacement_job.stage = "analyze" if frozen.wallpaper_path else progress.phase
            await _event(db, user_id, "companion.scene.updated", scene_id)
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
                selection=frozen.selection,
            )
            frozen.wallpaper_path = wallpaper.path
            frozen.source_size = wallpaper.source_size
            await save_progress(state)
        description = await _describe_image(user_id, frozen.wallpaper_path)
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            row = await get_scene(db, user_id, scene_id)
            if (
                row is None
                or row.replacement_job is None
                or row.replacement_job.availability != "pending"
                or row.replacement_job.token != task_id
            ):
                return
            old_path = row.media_path
            row.media_path = frozen.wallpaper_path
            origin = state.selected_origin()
            row.generation_origin_json = origin.model_dump(mode="json") if origin else None
            row.description = description.description
            row.prompt = frozen.prompt
            row.source = SceneSource.GENERATED.value
            row.reference_image = ""
            row.target_size_json = frozen.target_size.model_dump(mode="json")
            row.source_size_json = frozen.source_size.model_dump(mode="json") if frozen.source_size else None
            row.image_size_json = frozen.target_size.model_dump(mode="json")
            if row.initial_job is not None:
                row.initial_job.checkpoint = None
            row.replacement_job.availability = "ready"
            row.replacement_job.stage = "complete"
            row.replacement_job.error = None
            row.replacement_job.checkpoint = None
            await enqueue_asset_cleanup(db, user_id, state.stored_paths() | ({old_path} if old_path else set()))
            await _event(db, user_id, "companion.scene.updated", scene_id)
            await db.commit()
    finally:
        async with SESSION_LOCAL() as db:
            retained = await db.scalar(
                select(CompanionScene.id).where(
                    CompanionScene.user_id == user_id,
                    CompanionScene.id == scene_id,
                    CompanionScene.replacement_job.has((SceneJob.token == task_id) & SceneJob.checkpoint.is_not(None)),
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
        if (
            row is None
            or row.replacement_job is None
            or row.replacement_job.availability != "pending"
            or row.replacement_job.token != task_id
        ):
            return
        current = (
            SceneRegenerationState.model_validate(row.replacement_job.checkpoint)
            if row.replacement_job.checkpoint
            else None
        )
        retry_analysis = current is not None and (bool(current.wallpaper_path) or review_failed)
        if current and current.image_chain.phase == "submitting":
            error = "生图提交结果未知，未自动重复付费请求；请核对供应商任务后再决定是否重新生成"
        unknown = current is not None and (
            current.image_chain.phase == "submitting" or current.image_chain.stop_reason == "result_unknown"
        )
        if not retry_analysis and not unknown:
            cleanup_paths = current.image_chain.stored_paths() if current else set()
            row.replacement_job.checkpoint = None
        row.replacement_job.availability = "failed"
        row.replacement_job.stage = "review_failed" if review_failed else "analyze" if retry_analysis else "failed"
        row.replacement_job.error = error
        if unknown and row.replacement_job is not None:
            row.replacement_job.state = "unknown"
        await _event(db, user_id, "companion.scene.updated", scene_id)
        await enqueue_asset_cleanup(db, user_id, cleanup_paths)
        await db.commit()
    await cleanup_user_assets(user_id)


async def _run_pipeline(scene_id: int, user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.initial_job is None or row.status != "pending":
            return
        if row.source == SceneSource.USER_UPLOAD.value and not row.media_path:
            return
        state = (
            ImageChainState.model_validate(row.initial_job.checkpoint)
            if row.initial_job.checkpoint
            else ImageChainState()
        )
        needs_image = not row.media_path
        prompt = row.prompt
        reference_image = row.reference_image or None
        if not row.target_size_json:
            raise SceneStateError("场景任务缺少已冻结的生活空间尺寸")
        target = SceneImageSize.model_validate(row.target_size_json)

    async def save_progress(progress: ImageChainState) -> None:
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            fresh = await get_scene(db, user_id, scene_id)
            if fresh is None or fresh.initial_job is None or fresh.status != "pending":
                raise asyncio.CancelledError
            previous = (
                ImageChainState.model_validate(fresh.initial_job.checkpoint) if fresh.initial_job.checkpoint else None
            )
            if progress.phase == "submitting" and (
                previous is None or previous.phase != "submitting" or previous.active_index != progress.active_index
            ):
                await _check_policy(db, await _persona(db, user_id), fresh.origin)
                if fresh.origin == SceneOrigin.LLM.value:
                    if fresh.initial_job.attempt_count == 0:
                        await _consume_llm_quota(db, user_id)
                    await record_scene_submission(
                        db,
                        user_id,
                        fresh.initial_job.id,
                        fresh.initial_job.token,
                        fresh.initial_job.attempt_count,
                        progress.providers[progress.active_index or 0],
                    )
                fresh.initial_job.attempt_count += 1
            fresh.initial_job.checkpoint = progress.model_dump(mode="json")
            fresh.initial_job.stage = "analyze" if progress.phase == "complete" else progress.phase
            await _event(db, user_id, "companion.scene.updated", scene_id)
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
            if fresh is None or fresh.initial_job is None or fresh.status != "pending":
                await _reclaim_assets(user_id, {wallpaper.path})
                return
            fresh.media_path = wallpaper.path
            origin = state.selected_origin()
            fresh.generation_origin_json = origin.model_dump(mode="json") if origin else None
            fresh.target_size_json = target.model_dump(mode="json")
            fresh.source_size_json = wallpaper.source_size.model_dump(mode="json")
            fresh.image_size_json = wallpaper.image_size.model_dump(mode="json")
            fresh.initial_job.checkpoint = state.model_dump(mode="json")
            fresh.initial_job.stage = "analyze"
            await _event(db, user_id, "companion.scene.updated", scene_id)
            await db.commit()
    await _analyze(user_id, scene_id)


async def _mark_failed(user_id: int, scene_id: int, error: str, *, review_failed: bool = False) -> None:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.initial_job is None or row.status != "pending":
            return
        row.status = SceneStatus.DESCRIPTION_FAILED.value if row.media_path else SceneStatus.FAILED.value
        row.initial_job.error = error
        if row.initial_job.checkpoint:
            progress = ImageChainState.model_validate(row.initial_job.checkpoint)
            if progress.phase == "submitting" or progress.stop_reason == "result_unknown":
                row.initial_job.state = "unknown"
        if review_failed:
            row.initial_job.stage = "review_failed"
        await _event(db, user_id, "companion.scene.updated", scene_id)
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
            elif row and row.initial_job is not None and row.initial_job.stage == "submitting":
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
        if row is None or row.initial_job is None or row.status != "pending" or row.initial_job.state == "unknown":
            return False
        if row.initial_job.stage == "waiting_upload":
            return True
        resumable = bool(row.media_path or row.initial_job.checkpoint or row.initial_job.stage == "prepare")
    if resumable:
        _launch_task(scene_id, user_id)
    else:
        await _mark_failed(user_id, scene_id, "任务已中断；结果未知的付费请求不会自动重发，请核对后重新创建")
    return resumable


async def _resume_scene_row(row: CompanionScene) -> None:
    # 同行双 pending 不可达；优先恢复重新生成，缺任务或维护中时回退整单重跑。
    if (
        row.replacement_job is not None
        and row.replacement_job.availability == "pending"
        and row.replacement_job.token
        and row.replacement_job.state != "unknown"
        and not is_user_in_maintenance(row.user_id)
    ):
        _launch_task(row.id, row.user_id, regeneration_task_id=row.replacement_job.token)
    elif row.status == "pending":
        await resume_scene_generation(row.user_id, row.id)


async def resume_scene_jobs() -> None:
    async with SESSION_LOCAL() as db:
        rows = (
            await db.scalars(
                select(CompanionScene).where(
                    or_(
                        CompanionScene.status == "pending",
                        CompanionScene.replacement_job.has(SceneJob.availability == "pending"),
                    ),
                ),
            )
        ).all()
    for row in rows:
        await _resume_scene_row(row)


async def _scene_paid_boundary(user_id: int) -> None:
    require_new_generation_call(user_id)


async def resume_user_scene_jobs(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        rows = (
            await db.scalars(
                select(CompanionScene).where(
                    CompanionScene.user_id == user_id,
                    or_(
                        CompanionScene.status == "pending",
                        CompanionScene.replacement_job.has(SceneJob.availability == "pending"),
                    ),
                ),
            )
        ).all()
    for row in rows:
        await _resume_scene_row(row)


_INITIAL_SCENE_DEFAULT_NOTES = "整洁宁静的日常起居空间，光线柔和，桌椅、床铺与少量个人物品摆放合理。"


def _initial_scene_notes(persona: PersonaSnapshot) -> str:
    """初始房间只用性格影响氛围；说话风格、关系与用户资料不是画面信息，关系还可能引出第二个人物。"""
    personality = load_persona_definition(persona).get("personality", "").strip()
    if not personality:
        return _INITIAL_SCENE_DEFAULT_NOTES
    return f"{_INITIAL_SCENE_DEFAULT_NOTES}房间的氛围与摆设可以体现伙伴的性格：{personality[:200]}。"


async def schedule_initial_scene(user_id: int) -> SceneSnapshot | None:
    async with SESSION_LOCAL() as db:
        persona = await persona_snapshot(db, user_id)
        if persona is None or not persona.is_complete:
            return None
        if await db.scalar(select(CompanionScene.id).where(CompanionScene.user_id == user_id).limit(1)):
            return None
        notes = _initial_scene_notes(persona)
    try:
        row = await schedule_scene_generation(
            user_id,
            origin=SceneOrigin.ONBOARDING.value,
            notes=notes,
        )
        return scene_view(row)
    except SceneStateError as exc:
        logger.warning("initial scene not scheduled", extra={"user_id": user_id, "error": str(exc)})
        return None


async def drain_scene_jobs() -> None:
    _QUEUED_TASKS.clear()
    await cancel_and_wait(list(_INFLIGHT_TASKS.values()))
    _INFLIGHT_TASKS.clear()


async def _prepare_reference_image(data: bytes) -> str:
    """校验用户参考图并按实际格式冻结为 data URI。"""
    try:
        data, mime = await asyncio.to_thread(validate_image_bytes, data)
    except Exception as exc:
        raise SceneError("参考图无法读取，请选择有效图片") from exc
    return await asyncio.to_thread(build_data_uri, data, mime)
