"""动作制作：commands。"""

import contextlib
import json

from infrastructure.ai.llm_client import resolve_vision_chain
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import unlink_companion_asset
from infrastructure.text import safe_json_loads
from infrastructure.video_processing.process import MAX_CANVAS_HEIGHT, MAX_CANVAS_WIDTH, MAX_SOURCE_BYTES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.command_contracts import ActionPackSnapshot
from features.actions.generation.state import GenerationContext
from features.actions.models import (
    REQUIRED_SYSTEM_SLOTS,
    SYSTEM_ACTION_MEDIA_TYPES,
    SYSTEM_SLOTS,
    CompanionAction,
    CompanionActionPack,
)
from features.actions.policy import get_action_accept_lock
from features.appearance.avatar_service import get_avatar_job_lock
from features.appearance.character_card import (
    character_snapshot_is_current,
    render_character_profile,
    require_character_snapshot,
)
from features.appearance.queries import (
    mark_initial_video_started,
    outfit_identity_matches,
    outfit_snapshot,
    ready_outfit_snapshot,
)
from features.companion.persona_service import load_persona_definition
from features.companion.visual_state import ensure_persona_snapshot
from features.media.chain import FrozenMediaProvider, spawn_removed_action_task_cancellation
from features.media.image_generation import resolve_image_gen_chain
from features.media.review_service import list_rejected_action_media

from .catalog import _publish_dynamic_catalog
from .errors import VideoPackError, VideoPackNotFoundError, VideoPackStateError
from .execution_state import _ACTION_INFLIGHT, _DEFAULT_CANVAS, _SYSTEM_ACTION_SECONDS
from .helpers import _artifact_abs_path, _copy_portrait_asset, _pack_dir, _reference_hash
from .queries import (
    _action_order,
    _active_avatar,
    _active_outfit_id,
    _can_publish_jobs,
    _can_resume_job,
    _get_pack,
    _has_pending_work,
    _jobs_by_pack,
    _load_generation_context,
    _resumable_action_state,
)
from .references import (
    _freeze_action_video_plan,
    _reject_concurrent_build,
    _reject_pack_regeneration,
    _require_current_identity_image,
    _resolve_action_video_plan,
    require_action_matting_model,
)
from .tasks import _kick_build, _kick_generate, _queue_in_place_redo, kick_dynamic_action
from .transactions import _activate_locked, _create_or_reset_pack, _remove_packs


async def _system_action_row(
    db: AsyncSession,
    pack: CompanionActionPack,
    action: str,
    *,
    media_type: str,
) -> CompanionAction:
    job = await db.scalar(
        select(CompanionAction).where(CompanionAction.pack_id == pack.id, CompanionAction.key == action),
    )
    if job is None:
        job = CompanionAction(user_id=pack.user_id, pack_id=pack.id, outfit_id=pack.outfit_id, key=action)
        db.add(job)
    job.system_slot, job.media_type = action, media_type
    job.kind = "loop" if media_type == "video" else None
    job.target_duration_seconds = job.actual_duration_ms = job.frames = job.loopable = job.hitmask_fps = None
    job.reference_hash = pack.reference_hash
    job.status, job.error = "queued", None
    return job


async def create_pack_from_clips(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int,
    clips: dict[str, tuple[bytes, str]],
    canvas: tuple[int, int] = _DEFAULT_CANVAS,
    action_ranges: dict[str, tuple[float, float]] | None = None,
) -> ActionPackSnapshot:
    """上传动作素材创建动作包；处理在后台进行。clips 为「动作键 → (源字节, MIME)」，action_ranges 提供长视频起止秒。失败只影响本次包。"""
    if not set(REQUIRED_SYSTEM_SLOTS) <= set(clips) or not set(clips) <= set(SYSTEM_SLOTS):
        raise VideoPackError("片段须包含待机与拖拽，且只能是已知动作")
    canvas_w, canvas_h = canvas
    if not (0 < canvas_w <= MAX_CANVAS_WIDTH and 0 < canvas_h <= MAX_CANVAS_HEIGHT) or canvas_w % 2 or canvas_h % 2:
        raise VideoPackError("画布尺寸超出限制")

    async with get_avatar_job_lock(user_id):
        persona = await ensure_persona_snapshot(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成角色设定再创建动作包")
        outfit = await outfit_snapshot(db, user_id, outfit_id)
        if outfit is None:
            raise VideoPackError("找不到对应的外观")
        if outfit.status != "ready":
            raise VideoPackStateError("外观尚未确认，无法创建动作包")
        await _reject_concurrent_build(db, user_id, outfit.id)
        await _reject_pack_regeneration(db, user_id, outfit.id)
        avatar = await _active_avatar(db, user_id)
        reference_hash = await _reference_hash(outfit, avatar)

        for action, (data, _content_type) in clips.items():
            if len(data) > MAX_SOURCE_BYTES:
                raise VideoPackError("动作片段超过大小上限")

        pack = await _create_or_reset_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
        pack.canvas_spec = json.dumps({"width": canvas_w, "height": canvas_h})
        for action, (_data, content_type) in clips.items():
            media_type = "image" if content_type.startswith("image/") else "video"
            job = await _system_action_row(db, pack, action, media_type=media_type)
            job.stage = "process"
        await db.commit()
        await db.refresh(pack)

    _kick_build(
        pack.id,
        user_id,
        clips=clips,
        canvas=(canvas_w, canvas_h),
        action_ranges=action_ranges or {},
    )
    return ActionPackSnapshot.model_validate(pack)


async def create_pack_from_reference(
    db: AsyncSession,
    user_id: int,
    *,
    outfit_id: int | None = None,
    force: bool = False,
    create_only: bool = False,
    source_pack_id: int | None = None,
    action: str | None = None,
    feedback: str = "",
) -> ActionPackSnapshot:
    """动作原地重做；已有就绪动作时只允许单项制作，不新建整包。"""
    if (source_pack_id is None) != (action is None) or (action is not None and not action.strip()):
        raise VideoPackStateError("单动作请求必须指定有效动作包与动作")
    async with (
        get_action_accept_lock(user_id),
        get_avatar_job_lock(user_id),
        contextlib.AsyncExitStack() as pending_assets,
    ):
        persona = await ensure_persona_snapshot(db, user_id)
        if not persona.is_complete:
            raise VideoPackStateError("请先完成角色设定")
        source = await _get_pack(db, user_id, source_pack_id) if source_pack_id is not None else None
        if source_pack_id is not None and (
            source is None or source.status not in ("processing", "ready", "failed") or not source.reference_path
        ):
            raise VideoPackStateError("该动作包不能生成该动作")
        if source is not None and action is not None:
            require_action_matting_model()
            source_context = _load_generation_context(source)
            if source_context is None:
                raise VideoPackStateError("该动作包的制作资料不完整，请重新生成动作包")
            if source_context.reference_alignment != "ready":
                raise VideoPackStateError("动作参考尚未准备完成，请等待原任务")
            await _require_current_identity_image(source_context, await _active_avatar(db, user_id))
            job = await _queue_in_place_redo(db, source, action, feedback)
            if source.status == "failed":
                source.status, source.error = "processing", None
            await db.commit()
            kick_dynamic_action(source.id, job.id, user_id)
            await db.refresh(source)
            return ActionPackSnapshot.model_validate(source)
        outfit = await ready_outfit_snapshot(db, user_id, outfit_id)
        if outfit is None:
            raise VideoPackStateError("请先确认外观参考图")
        if create_only:
            existing = await db.scalar(
                select(CompanionActionPack).where(
                    CompanionActionPack.user_id == user_id,
                    CompanionActionPack.outfit_id == outfit.id,
                ),
            )
            if existing is not None:
                await mark_initial_video_started(db, user_id, outfit.id)
                await db.commit()
                return ActionPackSnapshot.model_validate(existing)
            if outfit.initial_video_started:
                raise VideoPackStateError("基础动作包任务已移除，请在衣柜重新准备")
        await _reject_concurrent_build(db, user_id, outfit.id)
        avatar = await _active_avatar(db, user_id)
        reference_hash = await _reference_hash(outfit, avatar)
        identity = await require_character_snapshot(db, user_id)
        seed_path = avatar.seed_fullbody_url if avatar else ""
        identity_reference_role = "portrait" if outfit_identity_matches(outfit.source_json, seed_path) else "fullbody"
        if not force:
            reusable = (
                await db.execute(
                    select(CompanionActionPack).where(
                        CompanionActionPack.user_id == user_id,
                        CompanionActionPack.outfit_id == outfit.id,
                        CompanionActionPack.reference_hash == reference_hash,
                        CompanionActionPack.status == "ready",
                        CompanionActionPack.reference_path != "",
                    ),
                )
            ).scalar_one_or_none()
            reusable_context = _load_generation_context(reusable) if reusable is not None else None
            if (
                reusable is not None
                and reusable_context is not None
                and reusable_context.identity == identity
                and reusable_context.identity_reference_role == identity_reference_role
                and await character_snapshot_is_current(db, user_id, identity)
            ):
                try:
                    await _require_current_identity_image(reusable_context, avatar)
                except VideoPackError:
                    pass
                else:
                    await db.commit()
                    await db.refresh(reusable)
                    return ActionPackSnapshot.model_validate(reusable)
        await _reject_pack_regeneration(db, user_id, outfit.id)
        system_seconds: float | None = None
        system_providers: list[FrozenMediaProvider] = []
        if not await resolve_vision_chain(db, user_id, task=AiTaskKey.window_action):
            raise VideoPackStateError("未配置视觉模型，无法根据角色参考图撰写动作脚本")
        alignment_chain = await resolve_image_gen_chain(
            db,
            user_id,
            task=AiTaskKey.window_action,
            has_reference=True,
            multiple_references=True,
        )
        if not alignment_chain.providers:
            raise VideoPackStateError(alignment_chain.error or "请配置支持头像与着装双参考的图像供应商")
        image_chain = await resolve_image_gen_chain(
            db,
            user_id,
            task=AiTaskKey.window_action,
            has_reference=True,
            image_edit=True,
        )
        if not image_chain.providers:
            raise VideoPackStateError(image_chain.error or "请配置图像编辑供应商以生成动作姿态")
        require_action_matting_model()
        pack = await _create_or_reset_pack(db, user_id, avatar=avatar, outfit=outfit, reference_hash=reference_hash)
        directory = _pack_dir(pack)
        reference_path = await _copy_portrait_asset(
            outfit.fullbody_url,
            user_id=user_id,
            label="video_reference",
            directory=directory,
        )
        if reference_path is None:
            raise VideoPackStateError("外观参考图不可读")
        pending_assets.callback(unlink_companion_asset, reference_path)
        active_outfit_id = await _active_outfit_id(db, user_id)
        identity_reference_path = await _copy_portrait_asset(
            seed_path,
            user_id=user_id,
            label="video_identity",
            directory=directory,
        )
        if identity_reference_path is None:
            raise VideoPackStateError("全身形象无法读取")
        pending_assets.callback(unlink_companion_asset, identity_reference_path)
        context = GenerationContext(
            identity=identity,
            identity_reference_path=identity_reference_path,
            identity_reference_role=identity_reference_role,
            reference_alignment="pending",
            persona_definition=load_persona_definition(persona),
            personality_tags=safe_json_loads(persona.personality_tags_json or "[]", default=[]),
            outfit_description=outfit.description or "",
            outfit_description_frozen=bool(outfit.description),
            feedback=feedback,
            active_outfit_id=outfit.id if create_only and outfit.is_initial else active_outfit_id,
            must_actions=list(REQUIRED_SYSTEM_SLOTS),
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
        for key in sorted(REQUIRED_SYSTEM_SLOTS, key=_action_order):
            media_type = SYSTEM_ACTION_MEDIA_TYPES[key]
            if media_type == "video" and not system_providers:
                system_seconds, system_providers = await _resolve_action_video_plan(
                    user_id,
                    needs_loop_frames=True,
                    duration_seconds=_SYSTEM_ACTION_SECONDS,
                )
            job = await _system_action_row(db, pack, key, media_type=media_type)
            job.stage = "script"
            if media_type == "video" and system_seconds is not None:
                _freeze_action_video_plan(job, system_seconds, system_providers)
        pack.context_json = context.model_dump_json()
        await db.commit()
        pending_assets.pop_all()
        await db.refresh(pack)
    _kick_generate(pack.id, user_id)
    return ActionPackSnapshot.model_validate(pack)


async def retry_pack(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    *,
    action: str | None = None,
) -> ActionPackSnapshot:
    """整包恢复已知任务；指定动作时只续下载、后处理或已有候选收尾。"""
    kick_action_id: int | None = None
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        allowed_statuses = ("failed",) if action is None else ("ready", "processing", "failed")
        if pack is None or pack.status not in allowed_statuses or not pack.reference_path:
            raise VideoPackStateError("该动作包没有可继续的任务")
        if action is None:
            await _reject_concurrent_build(db, user_id, pack.outfit_id)
        outfit = await outfit_snapshot(db, pack.user_id, pack.outfit_id)
        avatar = await _active_avatar(db, user_id)
        if outfit is None or avatar is None or pack.avatar_id != avatar.id:
            raise VideoPackStateError("该动作包对应的角色形象已切换，请重新生成动作包")
        if not _artifact_abs_path(pack.reference_path).is_file():
            raise VideoPackStateError("该动作包的参考图无法读取")
        context = _load_generation_context(pack)
        if context is None:
            raise VideoPackStateError("该动作包的制作资料不完整，请重新生成动作包")
        if context.reference_chain.phase == "submitting" or context.reference_chain.stop_reason == "result_unknown":
            raise VideoPackStateError("参考图生成结果尚未确认，请先核对原任务")
        jobs = list(
            (
                await db.execute(
                    select(CompanionAction)
                    .where(CompanionAction.pack_id == pack_id)
                    .with_for_update()
                    .execution_options(populate_existing=True),
                )
            ).scalars(),
        )
        targets = jobs if action is None else [job for job in jobs if job.key == action]
        if action is not None and not targets:
            raise VideoPackNotFoundError("找不到对应的动作，请刷新后重试")
        if any(job.id in _ACTION_INFLIGHT for job in targets):
            raise VideoPackStateError("动作任务仍在收尾，请等待完成后继续")
        rejected_media = await list_rejected_action_media(db, user_id, [job.id for job in targets])
        if action is not None:
            job = targets[0]
            state = _resumable_action_state(job, rejected_media.get(job.id, ()))
            if state is None or context.reference_alignment != "ready":
                raise VideoPackStateError("该动作没有可恢复的制作进度，请核对原任务或重新生成")
            require_action_matting_model()
            # 继续制作只收尾已有视频，跳过尚未提交的供应商，避免检查后追加视频生成。
            state.next_index = len(state.providers)
            if state.phase == "storing":
                state.stop_reason = ""
            job.artifact_path = next(
                (
                    path
                    for path in (job.artifact_path, state.source_path)
                    if path and _artifact_abs_path(path).is_file()
                ),
                None,
            )
            job.generation_state_json = state.model_dump_json()
            job.status, job.error = "queued", None
            kick_action_id = job.id
        else:
            recoverable = [job for job in jobs if _can_resume_job(job, rejected_media.get(job.id, ()))]
            if (
                not recoverable
                and not context.reference_chain.can_retry_goal_review()
                and not _can_publish_jobs(jobs, context.must_actions)
            ):
                raise VideoPackStateError("没有可继续的动作任务，请先核对原任务结果")
            if recoverable:
                require_action_matting_model()
            for job in recoverable:
                job.status, job.error = "queued", None
        if pack.status == "failed":
            pack.status, pack.error = "processing", None
        await db.commit()
        await db.refresh(pack)
    if kick_action_id is not None:
        kick_dynamic_action(pack.id, kick_action_id, user_id)
    else:
        _kick_generate(pack.id, user_id)
    return ActionPackSnapshot.model_validate(pack)


async def ensure_system_action(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    action: str,
) -> ActionPackSnapshot:
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
        if job is None or job.status == "queued" or (job.status == "processing" and job.id not in _ACTION_INFLIGHT):
            require_action_matting_model()
            if job is None:
                job = CompanionAction(
                    user_id=user_id,
                    pack_id=pack.id,
                    outfit_id=pack.outfit_id,
                    key=action,
                    name="",
                    system_slot=action,
                    media_type=SYSTEM_ACTION_MEDIA_TYPES[action],
                    status="queued",
                    stage="design",
                    reference_hash=pack.reference_hash,
                )
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
    return ActionPackSnapshot.model_validate(pack)


async def activate_pack(db: AsyncSession, user_id: int, pack_id: int) -> ActionPackSnapshot:
    async with get_avatar_job_lock(user_id):
        pack = await _get_pack(db, user_id, pack_id)
        if pack is None:
            raise VideoPackNotFoundError("找不到动作包")
        if pack.status != "ready":
            raise VideoPackStateError("动作包尚未就绪")
        outfit = await outfit_snapshot(db, pack.user_id, pack.outfit_id)
        avatar = await _active_avatar(db, user_id)
        if outfit is None or await _reference_hash(outfit, avatar) != pack.reference_hash:
            raise VideoPackStateError("该动作包对应的参考已变更")
        context = _load_generation_context(pack)
        if context is not None:
            await _require_current_identity_image(context, avatar)
        if pack.identity_review == "review":
            pack.identity_review = "accepted"
        await _activate_locked(db, pack)
        await db.commit()
        await db.refresh(pack)

    return ActionPackSnapshot.model_validate(pack)


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
