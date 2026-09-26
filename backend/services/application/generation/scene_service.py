"""场景资产生成、分析与启用；供应商等待不持有数据库会话。"""

import asyncio
import base64
import io
import json
from datetime import timedelta

from components import (
    DEFAULT_LANGUAGE,
    REMOTE_ASSET_DOWNLOAD_MAX_BYTES,
    SCENE_DOWNLOAD_MAX_BYTES,
    SCENE_FAILURES_TOTAL,
    SCENE_IMAGES_TOTAL,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    parse_llm_json,
    track_user_task,
    utc_now,
)
from modules.auth import User
from modules.companion import (
    AvatarAsset,
    CharacterCardSnapshot,
    CompanionOutfit,
    CompanionScene,
    Persona,
    SceneDescriptionRequest,
    SceneGenerationAttempt,
    SceneOrigin,
    ScenePolicy,
    SceneSource,
    SceneStatus,
)
from modules.settings import UserSetting
from modules.ws import emit_ws_event
from PIL import Image
from prompts.generation import SCENE_DESCRIBE_SYSTEM
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope
from services.domains.companion import (
    CharacterCardNotReadyError,
    character_snapshot_is_current,
    get_pending_scene,
    get_scene,
    load_character_snapshot,
    require_character_snapshot,
)
from services.domains.memory import read_user_profile
from services.infrastructure.assets import asset_store
from services.infrastructure.llm import resolve_reference_bytes, vision_chat

from .avatar_service import load_character_reference_data_uri
from .character_images import ImageChainState, drop_size_mismatched_candidates, generate_character_images
from .image_generation import ImageGenerationError
from .media_chain import MEDIA_IDENTITY_ACCEPT_SCORE
from .scene_prompt import ScenePromptContext, build_scene_prompt

logger = get_logger(__name__)
_SCENE_LOCKS: dict[int, asyncio.Lock] = {}
_INFLIGHT_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}
_AUTONOMOUS_ORIGINS = frozenset((SceneOrigin.LLM.value, SceneOrigin.NIGHTLY.value))


def scene_generation_wait_seconds(scene: CompanionScene) -> float:
    state = (
        ImageChainState.model_validate_json(scene.generation_state_json)
        if scene.generation_state_json
        else ImageChainState()
    )
    return max(
        900,
        len(state.providers)
        * (2 * SETTINGS.llm_request_timeout_seconds + 120 * max(1, SETTINGS.scene_store_max_attempts))
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
    if origin == SceneOrigin.NIGHTLY.value:
        user = await db.get(User, persona.user_id)
        if user is None or not user.nightly_activity_enabled:
            raise SceneLockedError("夜间自主活动已关闭")
    else:
        tier = await db.scalar(
            select(UserSetting.setting_value).where(
                UserSetting.user_id == persona.user_id,
                UserSetting.setting_key == "companion.disturbance_tier",
            ),
        )
        if tier in {"still", "silent"}:
            raise SceneLockedError("静止档不发起在线自主场景变化")


async def set_scene_policy(db: AsyncSession, user_id: int, policy: str) -> ScenePolicy:
    async with _scene_lock(user_id):
        persona = await _persona(db, user_id)
        persona.scene_policy = ScenePolicy(policy).value
        # 即使重新选择相同政策，也撤销尚未兑现的切换意图。
        persona.scene_switch_version += 1
        _event(db, persona, "companion.scene.updated")
        await db.commit()
    return ScenePolicy(policy)


async def _validate_ready(db: AsyncSession, user_id: int, row: CompanionScene) -> None:
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
    if not await character_snapshot_is_current(
        db,
        user_id,
        CharacterCardSnapshot.model_validate_json(row.character_card_json),
    ):
        raise SceneStateError("该场景的身份资料已过期，请创建符合当前身份的新场景")


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
        await _validate_ready(db, user_id, row)
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
    outfit_description: str | None = None,
) -> CompanionScene:
    outfit_description = (outfit_description or "").strip()
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        persona = await _persona(db, user_id)
        await _check_policy(db, persona, origin)
        if await get_pending_scene(db, user_id) is not None:
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
        try:
            identity = await require_character_snapshot(db, user_id)
        except CharacterCardNotReadyError as exc:
            raise SceneStateError(str(exc)) from exc
        avatar = await db.scalar(
            select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)),
        )
        if avatar is None or not avatar.seed_fullbody_url:
            raise SceneStateError("全身形象缺失，请重新生成全身参考图")
        if await asyncio.to_thread(load_character_reference_data_uri, avatar) is None:
            raise SceneStateError("全身参考图无法读取，请重新生成")
        if not outfit_description:
            outfit_description = await db.scalar(
                select(CompanionOutfit.description).where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.active.is_(True),
                    CompanionOutfit.status == "ready",
                ),
            )
        if auto_activate:
            persona.scene_switch_version += 1
        row = CompanionScene(
            user_id=user_id,
            status=SceneStatus.PENDING.value,
            stage="waiting_upload" if source == SceneSource.USER_UPLOAD.value else "prepare",
            origin=origin,
            source=source,
            requirements=notes,
            outfit_description=outfit_description or "",
            prompt=build_scene_prompt(
                ScenePromptContext(
                    notes=notes,
                    has_reference_image=bool(reference_image),
                    outfit_description=outfit_description or "",
                ),
            ),
            character_card_json=identity.model_dump_json(),
            secondary_reference_image=reference_image or "",
            seed_portrait_media_id=avatar.seed_fullbody_url,
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
    outfit_description: str | None = None,
    reference_image: str | None = None,
    auto_activate: bool = False,
) -> CompanionScene:
    if reference_image is not None:
        reference_image = await _prepare_reference_image(reference_image)
    row = await _new_scene(
        user_id,
        origin=origin,
        notes=notes or "",
        source=SceneSource.GENERATED.value,
        auto_activate=auto_activate,
        reference_image=reference_image,
        outfit_description=outfit_description,
    )
    _launch_task(row.id, user_id)
    return row


async def schedule_scene_prompt(
    user_id: int,
    *,
    notes: str | None = None,
    outfit_description: str | None = None,
) -> CompanionScene:
    return await _new_scene(
        user_id,
        origin=SceneOrigin.USER_REQUEST.value,
        notes=notes or "",
        source=SceneSource.USER_UPLOAD.value,
        auto_activate=False,
        outfit_description=outfit_description,
    )


async def adopt_scene(user_id: int, scene_id: int | None, *, data: bytes) -> CompanionScene:
    try:
        data, mime = await asyncio.to_thread(_decode_reference_image, data)
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
        row.stage = "store"
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()
    try:
        await _save_image(user_id, scene_id, data, mime)
    except Exception:
        await _mark_failed(user_id, scene_id, "图片保存失败，请重新上传")
        raise
    _launch_task(scene_id, user_id)
    async with SESSION_LOCAL() as db:
        saved = await get_scene(db, user_id, scene_id)
        if saved is None:
            raise SceneNotFoundError("找不到对应场景")
        return saved


async def discard_scene(user_id: int, scene_id: int) -> CompanionScene:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        if row.status != "pending":
            raise SceneStateError("该场景没有待取消的任务")
        persona = await _persona(db, user_id)
        row.status = SceneStatus.CANCELLED.value
        row.auto_activate = False
        if row.switch_version == persona.scene_switch_version:
            persona.scene_switch_version += 1
        _event(db, persona, "companion.scene.updated", scene_id)
        await db.commit()
        task = _INFLIGHT_TASKS.get((user_id, scene_id))
        if task and not task.done():
            task.cancel()
    if task:
        await asyncio.gather(task, return_exceptions=True)
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
        media_path = row.media_path
        state = (
            ImageChainState.model_validate_json(row.generation_state_json)
            if row.generation_state_json
            else ImageChainState()
        )
        await db.delete(row)
        _event(db, persona, "companion.scene.updated", scene_id)
        await db.commit()
    for path in {media_path, state.pending_path or "", *(candidate.path for candidate in state.candidates)} - {""}:
        await asyncio.to_thread(asset_store.unlink_companion_asset, path)


async def edit_scene_description(user_id: int, scene_id: int, description: SceneDescriptionRequest) -> CompanionScene:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        if not row.media_path:
            raise SceneStateError("图片尚未保存")
        # 人工补全不兑现旧的自动切换；用户明确选择启用。
        row.auto_activate = False
        row.title = description.title
        row.description = description.description
        row.status = SceneStatus.READY.value
        row.stage = "complete"
        row.ready_at = row.ready_at or utc_now()
        row.error = None
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()
        return row


async def retry_scene_description(user_id: int, scene_id: int) -> CompanionScene:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None:
            raise SceneNotFoundError("找不到对应场景")
        if not row.media_path or row.status not in {"description_failed", "cancelled"}:
            raise SceneStateError("该场景无需重试描述")
        if await get_pending_scene(db, user_id):
            raise SceneStateError("已有场景正在准备")
        row.status = "pending"
        row.stage = "analyze"
        row.error = None
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()
    _launch_task(scene_id, user_id)
    return row


async def _save_image(user_id: int, scene_id: int, data: bytes, mime: str) -> None:
    ext = {"image/gif": "gif", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "png")
    path = await asset_store.save_companion_asset_async(data, user_id=user_id, label="scene", ext=ext)
    committed = False
    try:
        async with _scene_lock(user_id), SESSION_LOCAL() as db:
            row = await get_scene(db, user_id, scene_id)
            if row is None or row.status != "pending":
                return
            row.media_path = path
            row.stage = "analyze"
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
            await db.commit()
            committed = True
    finally:
        if not committed:
            await asyncio.to_thread(asset_store.unlink_companion_asset, path)


async def _scene_image_uri(user_id: int, path: str) -> str:
    parsed_path = asset_store.parse_companion_asset_path(path)
    local = (
        asset_store.resolve_companion_asset_path(*parsed_path) if parsed_path and parsed_path[0] == user_id else None
    )
    if local is None:
        raise SceneStateError("场景图片无法读取")
    data, mime = await asyncio.to_thread(_decode_reference_image, await asyncio.to_thread(local[0].read_bytes))
    encoded = await asyncio.to_thread(base64.b64encode, data)
    return f"data:{mime};base64,{encoded.decode('ascii')}"


async def _analyze(user_id: int, scene_id: int) -> None:
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending" or not row.media_path:
            return
        path = row.media_path
        review_status = row.identity_review
        review_reason = row.identity_review_reason
        language = await db.scalar(
            select(UserSetting.setting_value).where(
                UserSetting.user_id == user_id,
                UserSetting.setting_key == "language",
            ),
        )
    data_uri = await _scene_image_uri(user_id, path)
    raw = await vision_chat(
        user_id,
        SCENE_DESCRIBE_SYSTEM,
        json.dumps({"output_language": language or DEFAULT_LANGUAGE}),
        reference_images=(data_uri,),
    )
    description = SceneDescriptionRequest.model_validate(parse_llm_json(raw))
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending" or row.media_path != path:
            return
        row.title = description.title
        row.description = description.description
        row.identity_review = review_status
        row.identity_review_reason = review_reason
        row.status = SceneStatus.READY.value
        row.stage = "complete"
        row.ready_at = utc_now()
        row.error = None
        persona = await _persona(db, user_id)
        _event(db, persona, "companion.scene.updated", scene_id)
        can_activate = row.auto_activate and row.switch_version == persona.scene_switch_version
        if can_activate:
            try:
                await _check_policy(db, persona, row.origin)
                await _validate_ready(db, user_id, row)
            except SceneError:
                can_activate = False
        if can_activate:
            persona.active_scene_id = row.id
            row.activated_at = utc_now()
            _event(db, persona, "companion.scene.activated", scene_id)
        await db.commit()
        SCENE_IMAGES_TOTAL.labels(origin=row.origin, result="ready").inc()
    return


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
        reference_image = row.secondary_reference_image or None
        identity_uri = state.inputs.identity_reference if state.inputs else None
        if needs_image and identity_uri is None:
            avatar = await db.get(
                AvatarAsset,
                CharacterCardSnapshot.model_validate_json(row.character_card_json).avatar_id,
            )
            if avatar is None or avatar.seed_fullbody_url != row.seed_portrait_media_id:
                raise SceneStateError("身份参考已变化，请重新创建场景")
            identity_uri = await asyncio.to_thread(load_character_reference_data_uri, avatar)
            if not identity_uri:
                raise SceneStateError("全身参考图无法读取，请重新生成")

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
                SCENE_IMAGES_TOTAL.labels(origin=fresh.origin, result="attempt").inc()
            fresh.generation_state_json = progress.model_dump_json()
            fresh.stage = progress.phase
            best = progress.best()
            if progress.phase == "complete" and best is not None:
                fresh.media_path = best.path
                fresh.stage = "analyze"
                fresh.identity_review = (
                    "pass" if best.score is not None and best.score >= MEDIA_IDENTITY_ACCEPT_SCORE else "auto_selected"
                )
                fresh.identity_review_reason = (
                    f"自动选择可用候选，身份一致性评分 {best.score if best.score is not None else '不可用'}"
                )
                if progress.stop_reason == "result_unknown":
                    fresh.identity_review_reason += "；后续提交结果未确认，已停止自动生成"
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
            await db.commit()

    if needs_image:
        await generate_character_images(
            prompt,
            size="1792x1024",
            user_id=user_id,
            reference_image=identity_uri,
            identity_reference=identity_uri,
            secondary_reference_image=reference_image,
            state=state,
            save_progress=save_progress,
            store_attempts=SETTINGS.scene_store_max_attempts,
            max_image_bytes=SCENE_DOWNLOAD_MAX_BYTES,
            size_enforced=True,
        )
    await _analyze(user_id, scene_id)


async def _mark_failed(user_id: int, scene_id: int, error: str) -> None:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending":
            return
        row.status = SceneStatus.DESCRIPTION_FAILED.value if row.media_path else SceneStatus.FAILED.value
        row.error = error
        _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()
        SCENE_FAILURES_TOTAL.labels(stage=row.stage).inc()
        SCENE_IMAGES_TOTAL.labels(origin=row.origin, result="failed").inc()


async def _restore_best_scene(user_id: int, scene_id: int) -> bool:
    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending" or row.media_path or not row.generation_state_json:
            return False
        original_state_json = row.generation_state_json
        state = ImageChainState.model_validate_json(original_state_json)

    if state.inputs is not None:
        state.inputs.size_enforced = True
    try:
        rejected_paths = await drop_size_mismatched_candidates(state)
    except ImageGenerationError:
        logger.warning(
            "scene candidate could not be size-verified; preserving it without promotion",
            extra={"scene_id": scene_id, "user_id": user_id},
            exc_info=True,
        )
        return False

    best = state.best()
    if best is not None:
        parsed = asset_store.parse_companion_asset_path(best.path)
        if parsed is None or asset_store.resolve_companion_asset_path(*parsed) is None:
            return False
        state.stop_reason = state.stop_reason or "generation_interrupted"
        state.phase = "complete"

    async with _scene_lock(user_id), SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
        if row is None or row.status != "pending" or row.media_path or row.generation_state_json != original_state_json:
            return False
        row.generation_state_json = state.model_dump_json()
        if best is not None:
            row.media_path = best.path
            row.stage = "analyze"
            row.identity_review = "auto_selected"
            row.identity_review_reason = "后续生成未完成，已保留评分最高的场景图片"
            _event(db, await _persona(db, user_id), "companion.scene.updated", scene_id)
        await db.commit()

    for path in rejected_paths:
        await asyncio.to_thread(asset_store.unlink_companion_asset, path)
    if best is not None:
        for candidate in state.candidates:
            if candidate.path != best.path:
                await asyncio.to_thread(asset_store.unlink_companion_asset, candidate.path)
    return best is not None


def _launch_task(scene_id: int, user_id: int) -> None:
    existing = _INFLIGHT_TASKS.get((user_id, scene_id))
    if existing is not None and not existing.done() and not existing.cancelling():
        return

    async def runner() -> None:
        try:
            await _run_pipeline(scene_id, user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("scene pipeline failed", extra={"scene_id": scene_id, "user_id": user_id})
            if await _restore_best_scene(user_id, scene_id):
                try:
                    await _run_pipeline(scene_id, user_id)
                    return
                except Exception:
                    logger.exception("best scene recovery failed", extra={"scene_id": scene_id, "user_id": user_id})
            async with SESSION_LOCAL() as db:
                row = await get_scene(db, user_id, scene_id)
                error = (
                    "图片已保存，描述分析失败；可重试分析或手动补全"
                    if row and row.media_path
                    else "生图结果未确认，未自动重发付费请求；请核对后再创建"
                    if row and row.stage == "submitting"
                    else "场景准备失败，请查看任务状态后重试"
                )
            await _mark_failed(user_id, scene_id, str(exc) if isinstance(exc, SceneError) else error)

    def completed(done: asyncio.Task[None]) -> None:
        if _INFLIGHT_TASKS.get((user_id, scene_id)) is done:
            _INFLIGHT_TASKS.pop((user_id, scene_id), None)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error("scene task interrupted", extra={"scene_id": scene_id, "user_id": user_id}, exc_info=error)

    task = asyncio.create_task(runner(), name=f"companion.scene.{user_id}.{scene_id}")
    _INFLIGHT_TASKS[user_id, scene_id] = task
    task.add_done_callback(completed)
    # 维护等待生成结果与描述落库，避免中断已提交的付费请求或遗留 pending 行。
    track_user_task(user_id, task, cancel_on_maintenance=False)


async def resume_scene_generation(user_id: int, scene_id: int) -> bool:
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


_INITIAL_SCENE_DEFAULT_NOTES = (
    "伙伴在自己的房间里自然地生活：整洁的日常起居空间，光线柔和，桌椅、床铺与少量个人物品摆放合理，"
    "伙伴正从事一项安静自然的日常活动。"
)
# 初始引导素材仅限这些字段；外貌与物种不用于推断兴趣、职业或经历。
_INITIAL_SCENE_SOURCE_FIELDS = ("personality", "speaking_style", "relationship")


def _initial_scene_notes(persona: Persona, user_profile: dict[str, str]) -> str:
    materials: list[str] = []
    definition = persona.definition_json
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except ValueError:
            definition = {}
    if isinstance(definition, dict):
        for field in _INITIAL_SCENE_SOURCE_FIELDS:
            value = str(definition.get(field) or "").strip()
            if value:
                materials.append(value)
    hobbies = str(user_profile.get("user_hobbies") or "").strip()
    if hobbies:
        materials.append(f"用户兴趣：{hobbies}")
    if not materials:
        return _INITIAL_SCENE_DEFAULT_NOTES
    return (
        "伙伴在自己的房间中自然地生活。以下是已确认的角色资料，仅用于设计与其中环境或活动相关的部分，"
        "不据此虚构兴趣、职业或经历：" + "；".join(materials)
    )


async def schedule_initial_scene(user_id: int) -> CompanionScene | None:
    async with SESSION_LOCAL() as db:
        persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
        if persona is None or not persona.is_complete:
            return None
        if await load_character_snapshot(db, user_id) is None:
            return None
        if await db.scalar(select(CompanionScene.id).where(CompanionScene.user_id == user_id).limit(1)):
            return None
        user_profile = await read_user_profile(db, MemoryScope(user_id, "companion"))
        notes = _initial_scene_notes(persona, user_profile)
    try:
        return await schedule_scene_generation(
            user_id,
            origin=SceneOrigin.ONBOARDING.value,
            notes=notes,
            auto_activate=True,
        )
    except SceneStateError:
        return None


async def drain_scene_jobs() -> None:
    tasks = list(_INFLIGHT_TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _INFLIGHT_TASKS.clear()


def _decode_reference_image(data: bytes) -> tuple[bytes, str]:
    if not data or len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
        raise ValueError("image size exceeds limit")
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise ValueError("unsupported image format")
        if Image.MAX_IMAGE_PIXELS is not None and image.width * image.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("image dimensions exceed limit")
        mime = Image.MIME[image.format]
        image.load()
    return data, mime


async def _prepare_reference_image(reference: str) -> str:
    try:
        if reference.startswith("data:"):
            header, separator, payload = reference.partition(",")
            if not separator or not header.endswith(";base64"):
                raise ValueError("invalid image data URI")
            data = await asyncio.to_thread(base64.b64decode, payload, validate=True)
        else:
            data, _ = await resolve_reference_bytes(reference)
        data, mime = await asyncio.to_thread(_decode_reference_image, data)
    except Exception as exc:
        raise SceneError("参考图无法读取，请选择有效图片") from exc
    encoded = await asyncio.to_thread(base64.b64encode, data)
    return f"data:{mime};base64,{encoded.decode('ascii')}"
