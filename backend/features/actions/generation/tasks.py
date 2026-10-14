"""动作制作：tasks。"""

import asyncio
from collections.abc import Coroutine

from infrastructure.database import SESSION_LOCAL
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.asset_retirement import action_asset_paths, retire_action_assets
from features.actions.models import SYSTEM_ACTION_MEDIA_TYPES, CompanionAction, CompanionActionPack
from features.actions.repository import clear_action_attempt
from features.media.chain import FrozenMediaProvider
from features.media.review_service import reject_pending_action_reviews

from .catalog import _retire_after_generation
from .errors import VideoPackStateError
from .execution import _build_pack, _generate_dynamic_actions, _generate_pack
from .execution_state import _GEN_INFLIGHT, _GEN_PENDING, _SYSTEM_ACTION_SECONDS, _TASKS, logger
from .queries import _load_generation_context
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
