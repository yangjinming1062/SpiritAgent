"""动作制作：tasks。"""

import asyncio
from collections.abc import Coroutine
from uuid import uuid4

from infrastructure.ai.providers.base import ServiceType
from infrastructure.ai.selection import ProviderSelection
from infrastructure.database import SESSION_LOCAL
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.asset_retirement import action_asset_paths, retire_action_assets
from features.actions.generation.script import VideoActionSpec
from features.actions.materials import accepted_action_asset
from features.actions.media_contracts import ActionResult
from features.actions.models import SYSTEM_ACTION_MEDIA_TYPES, CompanionAction, CompanionActionPack
from features.actions.policy import ActionPolicyError, check_can_accept, consume_create_slot
from features.actions.repository import clear_action_attempt, make_semantic_fingerprint
from features.media.chain import FrozenMediaProvider, MediaChainState
from features.media.character_images import ImageChainState
from features.media.review_service import reject_pending_action_reviews

from .errors import VideoPackStateError
from .execution import _build_pack, _generate_dynamic_actions, _generate_pack
from .execution_state import (
    _ACTION_INFLIGHT,
    _ACTION_PIPELINE_SEMAPHORES,
    _GEN_INFLIGHT,
    _GEN_TASK_COUNTS,
    _PACK_FINALIZE_LOCKS,
    _PACK_GEN_INFLIGHT,
    _PACK_GEN_PENDING,
    _SYSTEM_ACTION_SECONDS,
    _TASKS,
    logger,
)
from .planning import _action_spec
from .queries import _job_result_unresolved, _load_generation_context
from .references import _freeze_action_video_plan, _resolve_action_video_plan
from .transactions import _mark_pack_failed


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


def _start_pack_task(
    pack_id: int,
    user_id: int,
    coro: Coroutine[object, object, bool | None],
    *,
    name: str,
    drain_queue_after: bool,
    whole_pack: bool = False,
) -> None:
    """登记制作任务；同包并行任务全部收尾后才释放包级资产保护。"""
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    _GEN_INFLIGHT.add(pack_id)
    _GEN_TASK_COUNTS[pack_id] = _GEN_TASK_COUNTS.get(pack_id, 0) + 1
    if whole_pack:
        _PACK_GEN_INFLIGHT.add(pack_id)
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def _done(done: asyncio.Task[bool | None]) -> None:
        remaining = _GEN_TASK_COUNTS[pack_id] - 1
        if remaining:
            _GEN_TASK_COUNTS[pack_id] = remaining
        else:
            _GEN_TASK_COUNTS.pop(pack_id)
            _GEN_INFLIGHT.discard(pack_id)
            _ACTION_PIPELINE_SEMAPHORES.pop(pack_id, None)
            _PACK_FINALIZE_LOCKS.pop(pack_id, None)
        restart_pack = whole_pack and pack_id in _PACK_GEN_PENDING
        if whole_pack:
            _PACK_GEN_INFLIGHT.discard(pack_id)
            _PACK_GEN_PENDING.discard(pack_id)
        if done.cancelled():
            return
        if (exc := done.exception()) is not None:
            logger.error("video pack task crashed", extra={"pack_id": pack_id, "task": name}, exc_info=exc)
            return
        if restart_pack:
            _kick_generate(pack_id, user_id)
            return
        # 单动作失败不阻塞其他排队项；整批无进展时停止，避免空转或反复恢复未知结果。
        if drain_queue_after or done.result():
            _kick_dynamic_generation(pack_id, user_id, queued_only=True)

    task.add_done_callback(_done)


def _kick_generate(pack_id: int, user_id: int) -> None:
    if is_user_in_maintenance(user_id):
        return
    if pack_id in _PACK_GEN_INFLIGHT:
        _PACK_GEN_PENDING.add(pack_id)
        return
    _start_pack_task(
        pack_id,
        user_id,
        _generate_pack(pack_id),
        name=f"companion.video.generate.{pack_id}",
        drain_queue_after=True,
        whole_pack=True,
    )


async def _queue_in_place_redo(
    db: AsyncSession,
    pack: CompanionActionPack,
    action: str,
    feedback: str,
) -> CompanionAction:
    """同动作无结果时保留原尝试；动态动作沿用设计与身份，用户重做单独计额。"""
    job = (
        await db.execute(
            select(CompanionAction)
            .where(CompanionAction.pack_id == pack.id, CompanionAction.key == action)
            .with_for_update(),
        )
    ).scalar_one_or_none()
    if job is not None and (_job_result_unresolved(job) or job.id in _ACTION_INFLIGHT):
        raise VideoPackStateError("动作仍在制作中，请等待完成后再重新生成")
    accepted = accepted_action_asset(job) if job is not None else None
    current = (
        ActionResult.model_validate_json(job.result_json)
        if job is not None and job.result_json and job.media_path
        else accepted.parse_result()
        if accepted is not None
        else None
    )
    if current is None and job is not None and job.generation_state_json:
        state = MediaChainState.model_validate_json(job.generation_state_json)
        candidate = state.best()
        if candidate is not None and candidate.result_json:
            current = ActionResult.model_validate_json(candidate.result_json)
            pose = (
                ImageChainState.model_validate_json(job.pose_generation_state_json)
                if job.pose_generation_state_json
                else ImageChainState()
            )
            current = current.model_copy(
                update={
                    "image_origin": current.image_origin or pose.selected_origin(),
                    "video_origin": current.video_origin
                    or state.providers[candidate.attempt].generation_origin(ServiceType.video_gen),
                },
            )
    is_system = action in SYSTEM_ACTION_MEDIA_TYPES
    if job is None and not is_system:
        raise VideoPackStateError("找不到对应的动作，请刷新后重试")
    media_type = SYSTEM_ACTION_MEDIA_TYPES[action] if is_system else job.media_type
    kind = "loop" if is_system else job.kind
    requested_seconds = _SYSTEM_ACTION_SECONDS
    if not is_system:
        spec = _action_spec(job)
        if not isinstance(spec, VideoActionSpec):
            raise VideoPackStateError("该动作的制作规格不完整，请重新设计动作")
        requested_seconds = job.target_duration_seconds or spec.duration_seconds
    seconds: float | None = None
    providers: list[FrozenMediaProvider] = []
    if media_type == "video":
        seconds, providers = await _resolve_action_video_plan(
            pack.user_id,
            needs_loop_frames=kind == "loop",
            duration_seconds=requested_seconds,
            selection=ProviderSelection(origin=current.video_origin if current else None, mode="next"),
        )
    if not is_system:
        try:
            await check_can_accept(
                db,
                pack.user_id,
                source="user_requested",
                duration_seconds=requested_seconds,
                pack_id=pack.id,
                semantic_fingerprint=make_semantic_fingerprint(job.name, job.motion_description),
            )
            await consume_create_slot(
                db,
                pack.user_id,
                source="user_requested",
                creation_key=f"redo:{job.id}:{uuid4().hex}",
                action_id=job.id,
            )
        except ActionPolicyError as exc:
            raise VideoPackStateError(str(exc)) from exc
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
        await reject_pending_action_reviews(db, user_id=job.user_id, action_id=job.id, media_type=job.media_type)
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
        job.kind = kind
        _freeze_action_video_plan(job, seconds, providers)
    context = _load_generation_context(pack)
    if context is not None:
        context.action_feedback[action] = feedback.strip()[:1000]
        context.action_image_selection[action] = ProviderSelection(
            origin=current.image_origin if current else None,
            mode="next",
        )
        pack.context_json = context.model_dump_json()
    await db.flush()
    return job


def kick_dynamic_action(pack_id: int, action_id: int, user_id: int) -> None:
    """启动已落库的单动作任务，与同包其他动作共享制作并发预算。"""
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
    _start_pack_task(
        pack_id,
        user_id,
        _generate_dynamic_actions(pack_id, action_ids=action_ids, queued_only=queued_only),
        name=f"companion.video.dynamic.{pack_id}",
        drain_queue_after=action_ids is not None,
    )


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
