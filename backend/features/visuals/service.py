"""影像库的公开操作：制作受理、播放协调与呈现偏好。"""

import asyncio
from typing import Literal
from uuid import uuid4

from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.proactive_runtime import get_presentation_snapshot

from .creation import begin_opening_repair
from .generation import (
    cancel_visual_job,
    pause_visual_job,
    schedule_visual_job,
    visual_image_chain_exhausted,
    visual_progress_has_unknown_submission,
    visual_progress_has_video_work,
)
from .geometry import visual_aspect_ratio
from .models import VisualItem, VisualJob, VisualPlayback, VisualPresentation
from .policy import visual_autonomous_block_reason
from .queries import job_view, presentation_state
from .repository import (
    VisualStateError,
    clear_pending_command,
    discard_job_draft,
    dispatch_play_command,
    emit_playback_receipt,
    notify_visuals_updated,
    playback_row,
    presentation_row,
    presented_item,
    require_presentation_row,
    touch_presentation,
    visual_is_short_action,
    visual_item,
    visual_job_row,
    visual_playable,
    visual_progress,
    visual_snapshot,
)
from .schemas import (
    ComboSnapshot,
    PlaybackReceiptKind,
    PlaybackSource,
    PlaybackStatus,
    PresentedVisual,
    VisualJobProgress,
    VisualJobView,
    VisualMediaKind,
    VisualPlayCommand,
    VisualPresentationView,
    combo_snapshot_hash,
)

_LOCKS: dict[int, asyncio.Lock] = {}


def _lock(user_id: int) -> asyncio.Lock:
    return _LOCKS.setdefault(user_id, asyncio.Lock())


def _action_description(snapshot: ComboSnapshot) -> str:
    return f"{snapshot.action.name}：{snapshot.action.motion_description}"


def _customization_priority(feedback: str | None, video_feedback: str | None) -> Literal["general", "video"]:
    """只有视频要求被修改时，冲突维度才以视频要求为准。"""

    return "video" if (video_feedback or "").strip() and not (feedback or "").strip() else "general"


def _source_image(item: VisualItem) -> str | None:
    """重做沿用的合格起始图：视频是真实输入图，图片是成品本身。"""

    return item.input_image_path if item.media_kind == "video" else item.media_path


async def create_visual_job(
    user_id: int,
    *,
    combo: ComboSnapshot,
    target_kind: VisualMediaKind,
    source: Literal["user_requested", "autonomous"],
    feedback: str = "",
    video_feedback: str = "",
    request_id: str | None = None,
) -> VisualJobView:
    """受理一次新建制作；request_id 作为受理幂等键，重复提交返回同一工作项。"""

    if target_kind not in ("image", "video"):
        raise VisualStateError("不支持的影像类型")
    combo = combo.model_copy(update={"feedback": feedback, "video_feedback": video_feedback})
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        if source == "autonomous" and not state.autonomous_enabled:
            raise VisualStateError("伙伴自主制作已关闭")
        if request_id:
            existing = await _requested_job(db, user_id, request_id)
            if existing is not None:
                if (
                    existing.purpose != "create"
                    or existing.target_kind != target_kind
                    or combo_snapshot_hash(visual_snapshot(existing)) != combo_snapshot_hash(combo)
                ):
                    raise VisualStateError("这个创建请求已经用于另一次制作")
                return job_view(existing)
        pending = await db.scalars(
            select(VisualJob).where(
                VisualJob.user_id == user_id,
                VisualJob.purpose == "create",
                VisualJob.state.in_(("queued", "running", "paused", "unknown")),
                VisualJob.target_kind == target_kind,
            ),
        )
        for existing in pending:
            if (
                combo_snapshot_hash(visual_snapshot(existing)) == combo_snapshot_hash(combo)
                and existing.feedback == feedback
                and existing.video_feedback == video_feedback
            ):
                return job_view(existing)
        progress = VisualJobProgress(
            generation_id=request_id or uuid4().hex,
            target_width=combo.target_width,
            target_height=combo.target_height,
            source=source,
            customization_priority=_customization_priority(feedback, video_feedback),
            image_only=target_kind == "image",
            aspect_ratio=visual_aspect_ratio(combo.target_width, combo.target_height),
            duration_seconds=combo.action.duration_seconds,
            action_description=_action_description(combo),
            creation_plan=combo.plan,
        )
        job = VisualJob(
            user_id=user_id,
            purpose="create",
            target_kind=target_kind,
            snapshot_json=combo.model_dump_json(),
            feedback=feedback,
            video_feedback=video_feedback,
            state="queued",
            stage="prepare",
            progress_json=progress.model_dump_json(),
        )
        db.add(job)
        await db.flush()
        await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
    schedule_visual_job(user_id, job.id)
    return view


async def _requested_job(db: AsyncSession, user_id: int, request_id: str) -> VisualJob | None:
    """受理幂等核对：在该账户最近的工作项里查找同一 request_id 的制作进度。"""

    rows = await db.scalars(
        select(VisualJob)
        .where(VisualJob.user_id == user_id, VisualJob.progress_json.is_not(None))
        .order_by(VisualJob.id.desc())
        .limit(200),
    )
    for job in rows:
        progress = visual_progress(job)
        if progress is not None and progress.generation_id == request_id:
            return job
    return None


async def regenerate_visual(
    user_id: int,
    visual_id: int,
    *,
    expected_version: int,
    feedback: str | None = None,
    video_feedback: str | None = None,
) -> VisualJobView:
    """重做所选影像：沿用冻结组合与已保存要求，通用要求会重新准备起始画面。"""

    async with _lock(user_id), SESSION_LOCAL() as db:
        item = await visual_item(db, user_id, visual_id, lock=True)
        if item.version != expected_version:
            raise VisualStateError("影像已按新版本更新，请刷新后重试")
        if not visual_playable(item):
            raise VisualStateError("影像文件缺失，无法重做")
        snapshot = visual_snapshot(item)
        source_image = _source_image(item)
        general = snapshot.feedback if feedback is None else feedback.strip()
        video_requirements = snapshot.video_feedback if video_feedback is None else video_feedback.strip()
        changed_general = general != snapshot.feedback
        snapshot = snapshot.model_copy(update={"feedback": general, "video_feedback": video_requirements})
        pending = await db.scalar(
            select(VisualJob)
            .where(
                VisualJob.user_id == user_id,
                VisualJob.target_visual_id == item.id,
                VisualJob.state.in_(("queued", "running", "paused", "unknown")),
            )
            .order_by(VisualJob.id.desc())
            .limit(1),
        )
        if pending is not None:
            return job_view(pending)
        reuse = item.media_kind == "video" and not changed_general and source_image is not None
        progress = VisualJobProgress(
            generation_id=uuid4().hex,
            target_width=snapshot.target_width,
            target_height=snapshot.target_height,
            source="user_requested",
            customization_priority=_customization_priority(feedback, video_feedback),
            image_only=item.media_kind == "image",
            aspect_ratio=visual_aspect_ratio(snapshot.target_width, snapshot.target_height),
            duration_seconds=snapshot.action.duration_seconds,
            action_description=_action_description(snapshot),
            creation_plan=None if changed_general else snapshot.plan,
            source_image_path=source_image,
            previous_pose_path=None if reuse or source_image is None else source_image,
            pose_path=source_image if reuse else "",
            image_confirmed=reuse,
            regenerate_image=False,
        )
        job = VisualJob(
            user_id=user_id,
            target_visual_id=item.id,
            purpose="regenerate",
            target_kind=item.media_kind,
            expected_version=expected_version,
            snapshot_json=snapshot.model_dump_json(),
            feedback=general,
            video_feedback=video_requirements,
            state="queued",
            stage="prepare",
            progress_json=progress.model_dump_json(),
        )
        db.add(job)
        await db.flush()
        await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
    schedule_visual_job(user_id, job.id)
    return view


async def play_visual(
    user_id: int,
    visual_id: int,
    *,
    source: PlaybackSource,
    expected_version: int | None = None,
) -> VisualPlayCommand:
    async with SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        item = await visual_item(db, user_id, visual_id)
        if expected_version is not None and expected_version != item.version:
            raise VisualStateError("影像已按新版本更新，请刷新后重试")
        if not visual_playable(item):
            raise VisualStateError("影像文件缺失，请重新制作")
        snapshot = visual_snapshot(item)
        if source == "autonomous":
            blocked = await visual_autonomous_block_reason(db, state, item, snapshot=snapshot)
            if blocked is not None:
                raise VisualStateError(f"当前不适合自主播放：{blocked}")
        _play, command = await dispatch_play_command(db, user_id, item, source=source, snapshot=snapshot)
        await db.commit()
        return command


async def claim_playback(user_id: int, play_id: str, *, client_id: str) -> bool:
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        play = await playback_row(db, user_id, play_id)
        if play.status not in {"queued", "claimed"} or ensure_utc(play.expires_at) <= utc_now():
            return False
        if play.client_id is not None and play.client_id != client_id:
            return False
        item = await db.get(VisualItem, play.visual_id)
        presentation = get_presentation_snapshot(user_id)
        stale = (
            presentation is None
            or presentation.revision != play.presentation_epoch
            or play.presentation_epoch != state.presentation_epoch
            or play.state_revision != state.state_revision
            or item is None
            or item.version != play.visual_version
        )
        blocked = None
        if not stale and item is not None and play.source == "autonomous":
            blocked = await visual_autonomous_block_reason(
                db,
                state,
                item,
                snapshot=visual_snapshot(item),
                excluded_play_id=play.play_id,
            )
        if stale or blocked is not None:
            play.status = "expired"
            play.error = "播放指令已过期，请重新播放" if stale else f"当前不适合自主播放：{blocked}"
            emit_playback_receipt(db, user_id, play_id, "expired", None)
            clear_pending_command(state, play_id)
            await db.commit()
            return False
        play.client_id = client_id
        play.status = "claimed"
        await db.commit()
        return True


async def restore_presentation(user_id: int) -> VisualPlayCommand | None:
    """恢复持续呈现，不重播旧短动作，也不开始制作。"""
    async with _lock(user_id), SESSION_LOCAL() as db:
        presence = get_presentation_snapshot(user_id)
        if presence is None:
            raise VisualStateError("桌面呈现上下文尚未就绪")
        state = await presentation_row(db, user_id, lock=True)
        if state is None:
            return None
        if state.pending_command_json:
            pending = VisualPlayCommand.model_validate_json(state.pending_command_json)
            if (
                pending.presentation_epoch == presence.revision
                and pending.state_revision == state.state_revision
                and ensure_utc(pending.expires_at) > utc_now()
            ):
                return pending
        visual_id = (
            state.current_visual_id if presence.mode == "window" else state.loop_visual_id or state.current_visual_id
        )
        if visual_id is None:
            return None
        item = await visual_item(db, user_id, visual_id)
        if presence.mode == "desktop" and visual_is_short_action(item):
            if item.input_image_id is None:
                return None
            item = await visual_item(db, user_id, item.input_image_id)
        if not visual_playable(item):
            raise VisualStateError("恢复的影像文件缺失")
        _play, command = await dispatch_play_command(db, user_id, item, source="restore")
        await db.commit()
        return command


async def record_playback(
    user_id: int,
    play_id: str,
    *,
    status: PlaybackStatus | Literal["interrupted"],
    receipt_kind: PlaybackReceiptKind | None = None,
    client_id: str | None = None,
    error: str | None = None,
) -> None:
    """记录实际播放回执；静态投影如实记账，不冒充动作执行。中断按失败终态入账。"""

    if status not in {"started", "finished", "failed", "expired", "interrupted"}:
        raise VisualStateError("不支持的播放回执状态")
    terminal: PlaybackStatus = "failed" if status == "interrupted" else status
    async with _lock(user_id), SESSION_LOCAL() as db:
        play = await playback_row(db, user_id, play_id, lock=True)
        if not client_id or play.client_id != client_id:
            raise VisualStateError("这个播放由其他设备处理")
        if play.status in {"finished", "failed", "expired"}:
            return
        if status == "started":
            if play.status == "started":
                return
            state = await require_presentation_row(db, user_id)
            presentation = get_presentation_snapshot(user_id)
            item = await db.get(VisualItem, play.visual_id)
            if (
                play.status != "claimed"
                or presentation is None
                or presentation.revision != play.presentation_epoch
                or play.presentation_epoch != state.presentation_epoch
                or play.state_revision != state.state_revision
                or ensure_utc(play.expires_at) <= utc_now()
                or item is None
                or item.version != play.visual_version
                or not play.snapshot_json
            ):
                raise VisualStateError("播放指令已失效，不能记录为已呈现")
            expected_kind = "window_static" if presentation.mode == "window" else item.media_kind
            if receipt_kind != expected_kind:
                raise VisualStateError("播放回执与当前呈现方式不一致")
            frozen = PresentedVisual.model_validate_json(play.snapshot_json).model_copy(
                update={"receipt_kind": receipt_kind},
            )
            state.current_visual_id = item.id
            state.current_snapshot_json = frozen.model_dump_json()
            if item.media_kind == "image" or visual_snapshot(item).action.clip_kind == "loop":
                state.loop_visual_id = item.id
                state.loop_snapshot_json = state.current_snapshot_json
            clear_pending_command(state, play_id)
            play.status = "started"
            play.started_at = utc_now()
            play.receipt_kind = receipt_kind or play.receipt_kind
            emit_playback_receipt(db, user_id, play_id, "started", receipt_kind)
            await touch_presentation(db, user_id)
            await db.commit()
            return
        if status == "finished" and play.status != "started":
            raise VisualStateError("影像尚未实际呈现，不能记录完成")
        if receipt_kind is not None and play.receipt_kind is not None and receipt_kind != play.receipt_kind:
            raise VisualStateError("播放回执类型已经变化")
        play.status = terminal
        play.receipt_kind = receipt_kind or play.receipt_kind
        play.error = error
        state = await require_presentation_row(db, user_id)
        clear_pending_command(state, play_id)
        if await _settle_short_play(db, state, play):
            await touch_presentation(db, user_id)
        emit_playback_receipt(db, user_id, play_id, terminal, receipt_kind)
        await db.commit()


async def _settle_short_play(db: AsyncSession, state: VisualPresentation, play: VisualPlayback) -> bool:
    """短动作结束后回到持续影像；更新的播放已接管时不回退。"""

    item = await db.get(VisualItem, play.visual_id)
    if item is None or not visual_is_short_action(item):
        return False
    current = await presented_item(db, state)
    if (
        not isinstance(current, PresentedVisual)
        or current.play_id != play.play_id
        or current.receipt_kind == "window_static"
    ):
        return False
    state.current_visual_id = state.loop_visual_id
    state.current_snapshot_json = state.loop_snapshot_json
    return True


async def pause_job(user_id: int, job_id: int) -> VisualJobView:
    async with _lock(user_id), SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        progress = visual_progress(job)
        if job.state not in {"queued", "running"} or progress is None:
            return job_view(job)
        generation_id = progress.generation_id
    # 让在途任务先保存最后已知的供应商句柄，再写入用户暂停标记。
    await pause_visual_job(job_id)
    async with _lock(user_id), SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        progress = visual_progress(job)
        if progress is not None and progress.generation_id == generation_id and job.state in {"queued", "running"}:
            progress.paused_by_user = True
            progress.submission_unknown = visual_progress_has_unknown_submission(progress)
            progress.failure_reason = "result_unknown" if progress.submission_unknown else "temporary"
            job.progress_json = progress.model_dump_json()
            job.state, job.stage, job.error = "paused", "paused", None
            await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
        return view


async def resume_job(user_id: int, job_id: int) -> VisualJobView:
    async with _lock(user_id), SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        progress = visual_progress(job)
        if job.state == "running":
            return job_view(job)
        if job.state in {"succeeded", "cancelled"}:
            raise VisualStateError("这个工作项已结束，请按需重新发起制作")
        if progress is None:
            raise VisualStateError("制作草稿已失效")
        if visual_progress_has_unknown_submission(progress):
            raise VisualStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        if progress.failure_reason == "quality_failed":
            raise VisualStateError("成品未通过检查，请按需重新生成")
        if progress.failure_reason == "material_rejected":
            raise VisualStateError("生成的成品无法使用，请重新制作")
        if progress.failure_reason == "queue_stopped":
            raise VisualStateError("本次视频制作已结束，请重新制作视频")
        if progress.expand_image and visual_image_chain_exhausted(progress):
            raise VisualStateError("本次图片制作没有可用结果，请重新制作图片")
        allow_model_steps = True
        try:
            require_new_generation_call(user_id)
        except GenerationWorkPaused:
            if visual_progress_has_video_work(progress):
                allow_model_steps = False
            else:
                raise
        progress.paused_by_user = False
        progress.failure_reason = ""
        job.progress_json = progress.model_dump_json()
        job.state, job.stage, job.error = "running", "prepare", None
        await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
    schedule_visual_job(user_id, job_id, allow_model_steps=allow_model_steps)
    return view


async def cancel_job(user_id: int, job_id: int) -> VisualJobView:
    async with _lock(user_id), SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        if job.state in {"succeeded", "cancelled"}:
            return job_view(job)
        await discard_job_draft(db, user_id, job)
        job.state, job.stage, job.error = "cancelled", "cancelled", None
        await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
    cancel_visual_job(job_id)
    return view


async def retry_job(user_id: int, job_id: int) -> VisualJobView:
    async with _lock(user_id), SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        progress = visual_progress(job)
        if job.state in {"queued", "running"}:
            return job_view(job)
        if job.state == "paused":
            pass  # 交给下面的继续流程
        elif job.state in {"succeeded", "cancelled"}:
            raise VisualStateError("这个工作项已结束，请按需重新发起制作")
        elif job.state == "unknown" or (progress is not None and visual_progress_has_unknown_submission(progress)):
            raise VisualStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        if progress is None:
            raise VisualStateError("制作草稿已失效")
        if progress.failure_reason == "activity_unsupported":
            return job_view(job)
        new_image = job.state == "failed" and (
            progress.failure_reason in {"image_unsuitable", "review_invalid"}
            or bool(progress.image_only and visual_image_chain_exhausted(progress))
        )
        if new_image:
            reference = progress.pose_path or progress.previous_pose_path
            begin_opening_repair(
                job,
                progress,
                reason=progress.repair_reason or job.error or "重新准备满足活动要求的起始画面",
                reference_path=reference,
                new_budget=True,
            )
            require_new_generation_call(user_id)
            job.progress_json = progress.model_dump_json()
            job.state, job.stage, job.error = "running", "repairing", None
            await notify_visuals_updated(db, user_id)
            view = job_view(job)
            await db.commit()
        else:
            view = None
    if view is None:
        return await resume_job(user_id, job_id)
    schedule_visual_job(user_id, job_id)
    return view


async def update_presentation_preferences(
    user_id: int,
    *,
    pinned: bool | None = None,
    autonomous_enabled: bool | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
) -> VisualPresentationView:
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        if pinned is not None:
            state.pinned = pinned
        if autonomous_enabled is not None:
            state.autonomous_enabled = autonomous_enabled
        if (target_width is None) != (target_height is None):
            raise VisualStateError("目标画幅须同时给出宽和高")
        if target_width is not None and target_height is not None:
            state.target_width = target_width
            state.target_height = target_height
        await touch_presentation(db, user_id)
        await db.commit()
    return await presentation_state(user_id)
