"""动作制作：catalog。"""

from collections.abc import Sequence

from infrastructure.assets.asset_store import signed_companion_asset_url, unlink_companion_asset
from infrastructure.database import SESSION_LOCAL
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.generation.script import VideoActionScriptEntry, parse_action_script_entry
from features.actions.materials import accepted_action_asset
from features.actions.media_contracts import (
    ActionResult,
    VideoActionImageResponse,
    VideoActionVideoResponse,
    VideoPackResponse,
)
from features.actions.models import CompanionAction, CompanionActionPack
from features.actions.publishing import emit_catalog_changed
from features.actions.repository import clear_action_attempt
from features.actions.schemas import PeekGeometry, parse_content_rect
from features.actions.usage import fulfill_deferred_play_intents
from features.appearance.avatar_service import get_avatar_job_lock
from features.appearance.character_card import character_snapshot_is_current
from features.appearance.queries import outfit_snapshot
from features.assets.references import ACTION_ASSET_FIELDS
from features.media.chain import MediaChainState, spawn_removed_action_task_cancellation
from features.media.contracts import RemovedActionVideoTask

from .execution_state import logger
from .helpers import _copy_pack_value, _reference_hash
from .pipeline import _review_pack_identity
from .queries import (
    _action_order,
    _active_avatar,
    _active_outfit_id,
    _can_publish_jobs,
    _can_resume_job,
    _has_pending_work,
    _jobs_by_pack,
    _load_generation_context,
    _newer_ready_pack,
)
from .references import _require_new_paid_step, _same_generation_lineage
from .transactions import _activate_locked, _mark_pack_failed, _publish_catalog, _remove_packs


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
            pack,
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
        outfit = await outfit_snapshot(db, pack.user_id, pack.outfit_id) if pack.outfit_id is not None else None
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
