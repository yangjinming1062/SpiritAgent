"""影像库的公开操作：制作受理、播放协调与呈现偏好。"""

import asyncio
from typing import Literal
from uuid import uuid4

from infrastructure.assets.references import asset_paths
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.lifecycle import enqueue_asset_cleanup
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.jobs.service import bind_job_assets, bind_job_request, requested_job_id
from features.life.schemas import LifeStepRequest
from features.life.use_cases import presentation_revision_matches, require_step_submission

from .creation import begin_opening_repair, identity_score_passed
from .eligibility import automatic_media_ready
from .generation import (
    cancel_visual_job,
    pause_visual_job,
    resume_known_result,
    schedule_visual_job,
    visual_image_chain_exhausted,
    visual_progress_has_unknown_submission,
    visual_progress_has_video_work,
)
from .geometry import visual_aspect_ratio
from .life import bind_creation_step, restart_visual_steps, settle_visual_steps
from .models import VisualImportJob, VisualItem, VisualJob, VisualPlayback, VisualPresentation, VisualVersion
from .policy import visual_autonomous_block_reason
from .ports import request_base_motion_preparation
from .queries import frozen_stage_reference as _frozen_stage_reference
from .queries import job_view, presentation_state
from .repository import (
    VisualStateError,
    clear_pending_command,
    discard_job_draft,
    dispatch_play_command,
    emit_playback_receipt,
    notify_visuals_updated,
    playback_row,
    presentation_outfit,
    presentation_row,
    presented_item,
    presented_version,
    require_presentation_row,
    stage_composition,
    touch_presentation,
    version_view,
    visual_clip_kind,
    visual_is_short_action,
    visual_item,
    visual_job_row,
    visual_playable,
    visual_plays_as_loop,
    visual_progress,
    visual_snapshot,
)
from .schemas import (
    ActorClipRef,
    PlaybackReceiptKind,
    PlaybackSource,
    PlaybackStatus,
    PresentedVisual,
    StageComposition,
    StageCompositionLayout,
    StageIntent,
    StagePresentation,
    VisualCreationPlan,
    VisualCreationSource,
    VisualJobProgress,
    VisualJobView,
    VisualMediaKind,
    VisualPlayCommand,
    VisualPresentationView,
    VisualSnapshot,
    visual_snapshot_hash,
)
from .static_presentation import cancel_pending_command, restore_static_presentation

_LOCKS: dict[int, asyncio.Lock] = {}


class VisualPresentationConflictError(VisualStateError):
    """判断期间生活或呈现已变化，应按最新状态重评。"""


def _lock(user_id: int) -> asyncio.Lock:
    return _LOCKS.setdefault(user_id, asyncio.Lock())


def _action_description(snapshot: VisualSnapshot) -> str:
    source = snapshot.source
    if source.kind == "scene":
        return source.scene.title or "环境媒体"
    return f"{source.action.name}：{source.action.motion_description}"


def _with_feedback(snapshot: VisualSnapshot, feedback: str, video_feedback: str) -> VisualSnapshot:
    """要求写回冻结输入对应变体的 feedback 字段。"""

    source = snapshot.source
    return snapshot.model_copy(
        update={
            "source": source.model_copy(update={"feedback": feedback, "video_feedback": video_feedback}),
        },
    )


def _source_ids(snapshot: VisualSnapshot) -> dict[str, int | None]:
    source = snapshot.source
    return {
        "outfit_id": source.outfit.material_id if source.kind in ("actor", "cinematic") else None,
        "scene_id": source.scene.scene_id if source.kind in ("scene", "cinematic") else None,
        "action_id": source.action.action_id if source.kind in ("actor", "cinematic") else None,
    }


def _progress_duration(snapshot: VisualSnapshot) -> int:
    """环境媒体没有动作时长；沿视频供应商的最短可用档（当前统一 5 秒）。"""

    source = snapshot.source
    return source.action.duration_seconds if source.kind != "scene" else 5


def _progress_plan(snapshot: VisualSnapshot) -> VisualCreationPlan | None:
    source = snapshot.source
    return source.plan if source.kind in ("actor", "cinematic") else None


def _customization_priority(feedback: str | None, video_feedback: str | None) -> Literal["general", "video"]:
    """只有视频要求被修改时，冲突维度才以视频要求为准。"""

    return "video" if (video_feedback or "").strip() and not (feedback or "").strip() else "general"


def _source_image(item: VisualItem) -> str | None:
    """重做沿用的合格起始图：视频是真实输入图，图片是成品本身。"""

    return item.input_image_path if item.media_kind == "video" else item.media_path


def _refresh_stage_composition(
    state: VisualPresentation,
    item: PresentedVisual,
    *,
    selection: str = "base",
    life_revision: int | None = None,
    life_activity_type: str | None = None,
) -> None:
    """开播回执推进基础组合：actor 进入主体层、scene 进入环境层、cinematic 整片接管。

    环境媒体替换环境层时保留主体层；主体媒体替换主体层时保留环境层；完整影像
    同时清空分层引用，短动作结束后回退到 loop 指针恢复分层画面。持续画面的
    选择来源随组合记录：生活驱动的整片记录当时生活修订与活动类别，用户/伙伴
    点播记为 user，普通轮换与恢复回落 base；下一次生活意图变化据此重新核对。
    """

    current = StageComposition.model_validate(state.composition_json) if state.composition_json else None
    static = StagePresentation.model_validate(state.current_static_json) if state.current_static_json else None
    snapshot = visual_snapshot(item)
    src = snapshot.source
    outfit = (
        src.outfit
        if src.kind in ("actor", "cinematic") or (src.kind == "analysis" and src.asset_kind in ("actor", "cinematic"))
        else presentation_outfit(state)
    )
    scene = (
        src.scene
        if src.kind in ("scene", "cinematic") or (src.kind == "analysis" and src.asset_kind in ("scene", "cinematic"))
        else (static.scene if static else current.scene_material if current else None)
    )
    identity_path = (
        src.identity_path
        if src.kind in ("actor", "cinematic") or (src.kind == "analysis" and src.asset_kind in ("actor", "cinematic"))
        else (static.identity_path if static else current.identity_path if current else None)
    )
    identity_hash = (
        src.identity_hash
        if src.kind in ("actor", "cinematic") or (src.kind == "analysis" and src.asset_kind in ("actor", "cinematic"))
        else (static.identity_hash if static else current.identity_hash if current else None)
    )
    layout = (
        current.layout
        if current is not None
        else StageCompositionLayout(layer_order=["cinematic", "effects"])
        if item.asset_kind == "cinematic"
        else StageCompositionLayout()
    )
    # 生活上下文只挂在生活驱动且持续播放的整片上；短动作与一次性播放不携带。
    is_life_sustained = (
        selection == "life"
        and item.asset_kind == "cinematic"
        and visual_plays_as_loop(item, snapshot)
        and life_revision is not None
        and life_activity_type is not None
    )
    context = (
        {"selection_source": "life", "life_revision": life_revision, "life_activity_type": life_activity_type}
        if is_life_sustained
        else {"selection_source": "user" if selection == "user" else "base"}
    )
    if item.asset_kind == "cinematic":
        state.composition_json = StageComposition(
            mode="cinematic",
            cinematic=ActorClipRef(visual_id=item.id, version=item.version),
            outfit_material=outfit,
            scene_material=scene,
            identity_path=identity_path,
            identity_hash=identity_hash,
            layout=layout,
            **context,
        ).model_dump(mode="json")
        return
    layered = current if current is not None and current.mode == "layered" else None
    actor = layered.actor if layered else None
    scene_visual_id = layered.scene_visual_id if layered else None
    scene_version = layered.scene_version if layered else None
    if item.asset_kind == "actor":
        actor = ActorClipRef(visual_id=item.id, version=item.version)
    else:
        scene_visual_id, scene_version = item.id, item.version
    state.composition_json = StageComposition(
        mode="layered",
        scene_visual_id=scene_visual_id,
        scene_version=scene_version,
        actor=actor,
        outfit_material=outfit,
        scene_material=scene,
        identity_path=identity_path,
        identity_hash=identity_hash,
        layout=layout,
        **{"selection_source": context["selection_source"]},
    ).model_dump(mode="json")


async def create_visual_job(
    user_id: int,
    *,
    snapshot: VisualSnapshot,
    target_kind: VisualMediaKind,
    source: VisualCreationSource,
    feedback: str = "",
    video_feedback: str = "",
    request_id: str | None = None,
    life_step: LifeStepRequest | None = None,
) -> VisualJobView:
    """受理一次新建制作；request_id 作为受理幂等键，重复提交返回同一工作项。"""

    if target_kind not in ("image", "video"):
        raise VisualStateError("不支持的影像类型")
    if snapshot.source.analysis_pending:
        raise VisualStateError("影像内容尚未整理完成，请先完成分析")
    if snapshot.source.kind == "scene" and target_kind != "video":
        # 环境静态图就是已就绪素材本身，无需再走制作链。
        raise VisualStateError("环境已有静态图片，只有动态环境需要制作")
    if snapshot.source.kind == "actor" and target_kind != "video":
        raise VisualStateError("透明主体动画须制作视频")
    if life_step is not None and life_step.idempotency_key != request_id:
        raise VisualStateError("生活步骤与制作请求的身份必须一致")
    combo_hash = visual_snapshot_hash(_with_feedback(snapshot, feedback, video_feedback))
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        if source == "autonomous" and not state.autonomous_enabled:
            raise VisualStateError("伙伴自主制作已关闭")
        if request_id:
            existing = await _requested_job(db, user_id, request_id, combo_hash)
            if existing is not None:
                if existing.purpose != "create" or existing.target_kind != target_kind:
                    raise VisualStateError("这个创建请求已经用于另一次制作")
                if request_id:
                    await bind_job_request(db, user_id, existing.id, request_id, combo_hash)
                await bind_creation_step(db, user_id, existing, life_step)
                await db.commit()
                return job_view(existing)
        await require_step_submission(db, user_id, life_step)
        pending = await db.scalars(
            select(VisualJob).where(
                VisualJob.user_id == user_id,
                VisualJob.purpose == "create",
                VisualJob.state.in_(("queued", "running", "paused", "unknown")),
                VisualJob.target_kind == target_kind,
            ),
        )
        for existing in pending:
            if visual_snapshot_hash(visual_snapshot(existing)) == combo_hash:
                if request_id:
                    await bind_job_request(db, user_id, existing.id, request_id, combo_hash)
                await bind_creation_step(db, user_id, existing, life_step)
                await db.commit()
                return job_view(existing)
        progress = VisualJobProgress(
            generation_id=uuid4().hex,
            target_width=snapshot.target_width,
            target_height=snapshot.target_height,
            source=source,
            customization_priority=_customization_priority(feedback, video_feedback),
            image_only=target_kind == "image",
            aspect_ratio=visual_aspect_ratio(snapshot.target_width, snapshot.target_height),
            duration_seconds=_progress_duration(snapshot),
            action_description=_action_description(snapshot),
            creation_plan=_progress_plan(snapshot),
        )
        job = VisualJob(
            user_id=user_id,
            purpose="create",
            target_kind=target_kind,
            asset_kind=snapshot.kind,
            snapshot_json=_with_feedback(snapshot, feedback, video_feedback).model_dump(mode="json"),
            **_source_ids(snapshot),
            feedback=feedback,
            video_feedback=video_feedback,
            state="queued",
            stage="prepare",
            checkpoint=progress.model_dump(mode="json"),
        )
        db.add(job)
        await db.flush()
        await bind_job_assets(db, user_id, job.id, asset_paths(job.snapshot_json, user_id), "input")
        if request_id:
            await bind_job_request(db, user_id, job.id, request_id, combo_hash)
        await bind_creation_step(db, user_id, job, life_step)
        await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
    schedule_visual_job(user_id, job.id)
    return view


async def _requested_job(db: AsyncSession, user_id: int, request_id: str, fingerprint: str) -> VisualJob | None:
    try:
        job_id = await requested_job_id(db, user_id, request_id, fingerprint)
    except ValueError as exc:
        raise VisualStateError(str(exc)) from exc
    if job_id is None:
        return None
    return await db.scalar(select(VisualJob).where(VisualJob.user_id == user_id, VisualJob.id == job_id))


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
        if snapshot.source.analysis_pending:
            raise VisualStateError("影像内容尚未整理完成，请先完成分析")
        if snapshot.source.kind in ("actor", "cinematic") and snapshot.source.identity.avatar_id is None:
            raise VisualStateError("这份影像的角色资料已删除，请用当前角色重新制作")
        if snapshot.source.kind == "scene" and item.media_kind == "image":
            raise VisualStateError("环境静态图片是环境素材本身，请在环境库中重做")
        source_image = _source_image(item)
        general = snapshot.source.feedback if feedback is None else feedback.strip()
        video_requirements = snapshot.source.video_feedback if video_feedback is None else video_feedback.strip()
        changed_general = general != snapshot.source.feedback
        snapshot = _with_feedback(snapshot, general, video_requirements)
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
            duration_seconds=_progress_duration(snapshot),
            action_description=_action_description(snapshot),
            creation_plan=None if item.media_kind == "image" or changed_general else _progress_plan(snapshot),
            previous_pose_path=source_image if item.media_kind == "video" and not reuse else None,
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
            asset_kind=snapshot.kind,
            snapshot_json=snapshot.model_dump(mode="json"),
            **_source_ids(snapshot),
            feedback=general,
            video_feedback=video_requirements,
            state="queued",
            stage="prepare",
            checkpoint=progress.model_dump(mode="json"),
        )
        db.add(job)
        await db.flush()
        await bind_job_assets(db, user_id, job.id, asset_paths(job.snapshot_json, user_id), "input")
        await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
    schedule_visual_job(user_id, job.id)
    return view


def _play_origin(source: PlaybackSource) -> str:
    """网关入口的语义细分；与 PlaybackSource 一一对应。"""

    return {"manual": "user", "autonomous": "user", "life": "life", "restore": "restore"}[source]


async def _life_context_is_current(
    db: AsyncSession,
    user_id: int,
    state: VisualPresentation,
    life_revision: int | None,
    life_activity_type: str | None,
) -> bool:
    """生活驱动画面只能在其冻结的意图仍是当前意图时恢复或开播。"""

    intent = state.intent_json
    return bool(
        isinstance(intent, dict)
        and intent.get("activity_kind") != "intent"
        and intent.get("activity_type") != "idle"
        and intent.get("life_revision") == life_revision
        and intent.get("activity_type") == life_activity_type
        and await presentation_revision_matches(db, user_id, life_revision)
    )


async def _life_playback_is_current(
    db: AsyncSession, user_id: int, state: VisualPresentation, play: VisualPlayback
) -> bool:
    if play.selection != "life":
        return True
    return await _life_context_is_current(db, user_id, state, play.life_revision, play.life_activity_type)


async def play_visual(
    user_id: int,
    visual_id: int,
    *,
    source: PlaybackSource,
    origin: Literal["user", "life"] | None = None,
    version: int | None = None,
    expected_state_revision: int | None = None,
    expected_life_revision: int | None = None,
) -> VisualPlayCommand:
    async with SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        if (expected_state_revision is not None and state.state_revision != expected_state_revision) or (
            expected_life_revision is not None
            and not await presentation_revision_matches(db, user_id, expected_life_revision)
        ):
            raise VisualPresentationConflictError("活动或呈现已变化，请重新判断")
        item: VisualItem | PresentedVisual = await visual_item(db, user_id, visual_id)
        version_id = item.current_version_id
        if version is not None and version != item.version:
            selected = await db.scalar(
                select(VisualVersion).where(
                    VisualVersion.visual_id == visual_id,
                    VisualVersion.user_id == user_id,
                    VisualVersion.number == version,
                )
            )
            if selected is None:
                raise VisualStateError("影像版本已不可用，请刷新后重试")
            item, version_id = version_view(item, selected), selected.id
        if not visual_playable(item):
            raise VisualStateError("影像文件缺失，请重新制作")
        snapshot = visual_snapshot(item)
        if not snapshot.source.presentation_approved:
            raise VisualStateError("影像尚未通过呈现审核，可在影像库中预览")
        # 生活导演点播必须由当前有效意图驱动：生活已推进时不受理旧意图的选择。
        intent_revision: int | None = None
        intent_activity: str | None = None
        if source == "life":
            if origin != "life":
                raise VisualStateError("生活演出必须由舞台意图驱动")
            intent = StageIntent.model_validate(state.intent_json) if state.intent_json else None
            if intent is None or intent.life_revision != expected_life_revision:
                raise VisualPresentationConflictError("活动或呈现已变化，请重新判断")
            if intent.activity_kind == "intent" or intent.activity_type == "idle":
                raise VisualPresentationConflictError("当前没有可呈现的进行中活动")
            if not await presentation_revision_matches(db, user_id, intent.life_revision):
                raise VisualPresentationConflictError("活动或呈现已变化，请重新判断")
            intent_revision = intent.life_revision
            intent_activity = intent.activity_type
        selection = (
            "life"
            if source == "life"
            else "user"
            if source in ("manual", "autonomous") and origin == "user"
            else "base"
        )
        if source == "autonomous":
            blocked = await visual_autonomous_block_reason(db, state, item, snapshot=snapshot)
            if blocked is not None:
                raise VisualStateError(f"当前不适合自主播放：{blocked}")
        if source in ("manual", "autonomous", "life"):
            await _require_stage_media_match(state, snapshot)
        if source in ("autonomous", "life"):
            media_source = snapshot.source
            semantics = getattr(media_source, "media_semantics", None)
            if not automatic_media_ready(
                media_kind=item.media_kind,
                asset_kind=item.asset_kind,
                media_path=item.media_path,
                auto_selectable=semantics.auto_selectable if semantics is not None else None,
                clip_kind=visual_clip_kind(item, snapshot),
                body=item.body_json,
                presentation_approved=media_source.presentation_approved,
                scene_reference=getattr(media_source, "scene", None) is not None,
            ):
                raise VisualStateError("这个版本暂不支持自动演出，可以在影像库预览")
        _play, command = await dispatch_play_command(
            db,
            user_id,
            item,
            source=source,
            origin=origin or _play_origin(source),
            version_id=version_id,
            snapshot=snapshot,
            selection=selection,
            life_revision=intent_revision,
            life_activity_type=intent_activity,
        )
        await db.commit()
        return command


async def _require_stage_media_match(state: VisualPresentation, snapshot: VisualSnapshot) -> None:
    """客户端导演点播只允许当前舞台身份与造型的媒体，避免旧造型画面混入当前呈现。"""

    source = snapshot.source
    if source.kind == "scene" or (source.kind == "analysis" and source.asset_kind == "scene"):
        return
    outfit, identity_hash = _frozen_stage_reference(state)
    if outfit is None or identity_hash is None:
        raise VisualStateError("当前舞台尚未准备好，请稍后再试")
    if source.identity_hash != identity_hash:
        raise VisualStateError("这份影像与伙伴当前形象不符，请重新制作")
    if source.outfit is None or source.outfit.content_hash != outfit.content_hash:
        raise VisualStateError("这份影像与当前穿着的造型不符，请先换装或重新制作")


async def claim_playback(user_id: int, play_id: str, *, client_id: str) -> bool:
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        play = await playback_row(db, user_id, play_id)
        if play.status not in {"queued", "claimed"} or ensure_utc(play.expires_at) <= utc_now():
            return False
        if play.client_id is not None and play.client_id != client_id:
            return False
        item = await presented_version(db, user_id, play.version_id)
        presentation = get_presentation_snapshot(user_id)
        stale = (
            presentation is None
            or presentation.revision != play.presentation_epoch
            or play.presentation_epoch != state.presentation_epoch
            or play.state_revision != state.state_revision
            or item is None
            or item.version != play.visual_version
            or not await _life_playback_is_current(db, user_id, state, play)
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
        if presence is None or not get_user_proactive_record(user_id).available:
            raise VisualStateError("桌面呈现上下文尚未就绪")
        state = await presentation_row(db, user_id, lock=True)
        if state is None:
            return None
        if state.pending_command_json:
            pending = VisualPlayCommand.model_validate(state.pending_command_json)
            if presence.mode == "window" and pending.asset_kind == "cinematic":
                await cancel_pending_command(db, state)
                pending = None
            if (
                pending is not None
                and pending.presentation_epoch == presence.revision
                and pending.state_revision == state.state_revision
                and ensure_utc(pending.expires_at) > utc_now()
            ):
                pending_is_life = pending.source == "life" or (
                    pending.composition is not None and pending.composition.selection_source == "life"
                )
                pending_play = await db.scalar(
                    select(VisualPlayback).where(
                        VisualPlayback.user_id == user_id,
                        VisualPlayback.play_id == pending.play_id,
                    )
                )
                if not pending_is_life or (
                    pending_play is not None
                    and pending_play.status in {"queued", "claimed"}
                    and await _life_playback_is_current(db, user_id, state, pending_play)
                ):
                    return pending
                await cancel_pending_command(db, state)
        if await restore_static_presentation(db, user_id, state, presentation_epoch=presence.revision):
            await db.commit()
            return None
        composition = stage_composition(state)
        if (
            composition is not None
            and composition.selection_source == "life"
            and not await _life_context_is_current(
                db,
                user_id,
                state,
                composition.life_revision,
                composition.life_activity_type,
            )
        ):
            # 已结束的生活整片不能作为恢复目标；清掉失效指针后由客户端按当前目录
            # 解析普通基础舞台，避免重连再次派发同一段旧活动影像。
            state.composition_json = None
            state.current_visual_id = state.current_version_id = None
            state.loop_visual_id = state.loop_version_id = None
            state.current_snapshot_json = state.loop_snapshot_json = None
            await touch_presentation(db, user_id)
            await db.commit()
            return None
        # 完整场景只能由桌面宿主呈现。窗口模式沿用当前身份/造型可用的主体
        # 循环（由客户端目录解析），不能把 cinematic 指令误交给静态投影。
        if presence.mode == "window" and composition is not None and composition.mode == "cinematic":
            await cancel_pending_command(db, state)
            await db.commit()
            return None
        version_id = (
            state.current_version_id if presence.mode == "window" else state.loop_version_id or state.current_version_id
        )
        if presence.mode == "window" and composition is not None and composition.actor is not None:
            version_id = await db.scalar(
                select(VisualVersion.id).where(
                    VisualVersion.user_id == user_id,
                    VisualVersion.visual_id == composition.actor.visual_id,
                    VisualVersion.number == composition.actor.version,
                )
            )
        if version_id is None:
            return None
        item = await presented_version(db, user_id, version_id)
        if item is None:
            return None
        if visual_is_short_action(item):
            version_id = state.loop_version_id or item.input_image_version_id
            if version_id is None:
                return None
            item = await presented_version(db, user_id, version_id)
            if item is None or visual_is_short_action(item):
                return None
        if not visual_playable(item):
            raise VisualStateError("恢复的影像文件缺失")
        # 恢复沿用组合既有选择来源与生活上下文：生活驱动的整片持续画面在恢复后
        # 仍按生活演出核对，用户手选画面保持 user；恢复动作本身不重置来源。
        restored_selection = "base"
        restored_life_revision = None
        restored_life_activity = None
        if (
            composition is not None
            and item.asset_kind == "cinematic"
            and composition.cinematic is not None
            and composition.cinematic.visual_id == item.id
            and composition.cinematic.version == item.version
            and composition.selection_source in ("life", "user")
        ):
            restored_selection = composition.selection_source
            restored_life_revision = composition.life_revision if composition.selection_source == "life" else None
            restored_life_activity = composition.life_activity_type if composition.selection_source == "life" else None
        _play, command = await dispatch_play_command(
            db,
            user_id,
            item,
            source="restore",
            version_id=version_id,
            selection=restored_selection,
            life_revision=restored_life_revision,
            life_activity_type=restored_life_activity,
        )
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
        state = await require_presentation_row(db, user_id, lock=True)
        play = await playback_row(db, user_id, play_id)
        if not client_id or play.client_id != client_id:
            raise VisualStateError("这个播放由其他设备处理")
        if play.status in {"finished", "failed", "expired"}:
            return
        if status == "started":
            if play.status == "started":
                return
            presentation = get_presentation_snapshot(user_id)
            item = await presented_version(db, user_id, play.version_id)
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
                or not await _life_playback_is_current(db, user_id, state, play)
            ):
                raise VisualStateError("播放指令已失效，不能记录为已呈现")
            # 窗口模式有两种合格回执：透明主体视频按真实媒体种类记账，静态投影按 window_static。
            expected_kinds = (
                ({"window_static", "video"} if item.asset_kind == "actor" else {"window_static"})
                if presentation.mode == "window"
                else {item.media_kind}
            )
            if receipt_kind not in expected_kinds:
                raise VisualStateError("播放回执与当前呈现方式不一致")
            frozen = PresentedVisual.model_validate(play.snapshot_json).model_copy(
                update={"receipt_kind": receipt_kind, "presentation_epoch": play.presentation_epoch},
            )
            snapshot = visual_snapshot(frozen)
            source = snapshot.source
            outfit_changed = (
                visual_plays_as_loop(item)
                and source.kind in ("actor", "cinematic")
                and source.outfit != presentation_outfit(state)
            )
            if visual_plays_as_loop(item):
                _refresh_stage_composition(
                    state,
                    item,
                    selection=play.selection,
                    life_revision=play.life_revision,
                    life_activity_type=play.life_activity_type,
                )
            state.current_visual_id = item.id
            if visual_plays_as_loop(item):
                state.current_static_json = None
            state.pending_static_json = None
            state.current_version_id = play.version_id
            state.current_snapshot_json = frozen.model_dump(mode="json")
            if visual_plays_as_loop(item):
                state.loop_visual_id = item.id
                state.loop_snapshot_json = state.current_snapshot_json
                state.loop_version_id = play.version_id
            clear_pending_command(state, play_id)
            await db.execute(
                update(VisualPlayback)
                .where(
                    VisualPlayback.user_id == user_id,
                    VisualPlayback.play_id != play_id,
                    VisualPlayback.status == "started",
                )
                .values(status="failed", error="已由新的呈现接替", updated_at=utc_now())
            )
            play.status = "started"
            play.started_at = utc_now()
            play.receipt_kind = receipt_kind or play.receipt_kind
            emit_playback_receipt(db, user_id, play_id, "started", receipt_kind)
            await touch_presentation(db, user_id)
            await db.commit()
            if outfit_changed:
                request_base_motion_preparation(user_id)
            return
        if status == "finished" and play.status != "started":
            raise VisualStateError("影像尚未实际呈现，不能记录完成")
        if receipt_kind is not None and play.receipt_kind is not None and receipt_kind != play.receipt_kind:
            raise VisualStateError("播放回执类型已经变化")
        play.status = terminal
        play.receipt_kind = receipt_kind or play.receipt_kind
        play.error = error
        clear_pending_command(state, play_id)
        if await _settle_short_play(db, state, play):
            await touch_presentation(db, user_id)
        emit_playback_receipt(db, user_id, play_id, terminal, receipt_kind)
        await db.commit()


async def _settle_short_play(db: AsyncSession, state: VisualPresentation, play: VisualPlayback) -> bool:
    """短动作结束后回到持续影像；更新的播放已接管时不回退。"""

    item = await presented_version(db, state.user_id, play.version_id)
    if item is None or not visual_is_short_action(item):
        return False
    current = presented_item(state)
    if current is None or current.play_id != play.play_id or current.receipt_kind == "window_static":
        return False
    state.current_visual_id = state.loop_visual_id
    state.current_version_id = state.loop_version_id
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
            job.checkpoint = progress.model_dump(mode="json")
            job.state, job.stage, job.error = "paused", "paused", None
            await notify_visuals_updated(db, user_id)
        view = job_view(job)
        await db.commit()
        return view


async def resume_job(user_id: int, job_id: int) -> VisualJobView:
    async with _lock(user_id), SESSION_LOCAL() as db:
        job = await visual_job_row(db, user_id, job_id, lock=True)
        progress = visual_progress(job)
        was_failed = job.state in {"failed", "unknown"}
        if job.state == "running":
            return job_view(job)
        if job.state in {"succeeded", "cancelled"}:
            raise VisualStateError("这个工作项已结束，请按需重新发起制作")
        if progress is None:
            raise VisualStateError("制作草稿已失效")
        if job.state == "unknown" and progress.failure_reason != "result_download_failed":
            raise VisualStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        if progress.failure_reason == "review_blocked":
            raise VisualStateError("图片检查未通过服务审核，请调整制作要求或参考形象后重新制作")
        if visual_progress_has_unknown_submission(progress):
            raise VisualStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        if progress.failure_reason == "quality_failed":
            raise VisualStateError("成品未通过检查，请按需重新生成")
        if progress.failure_reason == "material_rejected":
            raise VisualStateError("生成的成品无法使用，请重新制作")
        if progress.failure_reason == "queue_stopped":
            raise VisualStateError("本次视频制作已结束，请重新制作视频")
        allow_model_steps = True
        try:
            require_new_generation_call(user_id)
        except GenerationWorkPaused:
            if visual_progress_has_video_work(progress):
                allow_model_steps = False
            else:
                raise
        progress.paused_by_user = False
        if progress.failure_reason == "result_download_failed":
            resume_known_result(progress)
        progress.failure_reason = ""
        job.checkpoint = progress.model_dump(mode="json")
        job.state, job.stage, job.error = "running", "prepare", None
        await notify_visuals_updated(db, user_id)
        view = job_view(job)
        if was_failed:
            await restart_visual_steps(db, user_id, job)
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
        await settle_visual_steps(db, user_id, job)
        await notify_visuals_updated(db, user_id)
        await db.flush()
        await db.refresh(job)
        view = job_view(job)
        await db.commit()
    cancel_visual_job(job_id)
    return view


async def delete_visual(user_id: int, visual_id: int) -> None:
    """删除未被呈现、播放或其它成品引用的用户上传影像。"""

    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        item = await visual_item(db, user_id, visual_id, lock=True)
        snapshot = visual_snapshot(item)
        if not snapshot.source.original_path:
            raise VisualStateError("只能删除用户上传的影像")
        if state.current_visual_id == visual_id or state.loop_visual_id == visual_id:
            raise VisualStateError("当前呈现正在使用这份影像，请先切换画面")
        composition = stage_composition(state)
        if composition is not None and (
            composition.scene_visual_id == visual_id
            or (composition.actor is not None and composition.actor.visual_id == visual_id)
            or (composition.cinematic is not None and composition.cinematic.visual_id == visual_id)
        ):
            raise VisualStateError("当前舞台组合正在使用这份影像，请先切换画面")
        if await db.scalar(
            select(VisualPlayback.id)
            .where(VisualPlayback.user_id == user_id, VisualPlayback.visual_id == visual_id)
            .limit(1)
        ):
            raise VisualStateError("这份影像已有播放记录，不能删除")
        if await db.scalar(
            select(VisualVersion.id)
            .where(VisualVersion.user_id == user_id, VisualVersion.input_image_id == visual_id)
            .limit(1)
        ):
            raise VisualStateError("这份影像已被其它成品作为起始画面引用，不能删除")
        if await db.scalar(
            select(VisualJob.id)
            .where(
                VisualJob.user_id == user_id,
                (VisualJob.target_visual_id == visual_id)
                | (VisualJob.result_visual_id == visual_id)
                | (VisualJob.result_image_id == visual_id),
            )
            .limit(1)
        ):
            raise VisualStateError("这份影像仍被制作记录引用，不能删除")
        import_job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.user_id == user_id, VisualImportJob.visual_id == visual_id)
            .with_for_update()
        )
        if import_job is not None and import_job.state in {"queued", "running", "paused", "unknown"}:
            raise VisualStateError("影像分析尚未结束，请先取消或等待完成")
        versions = list(
            await db.scalars(
                select(VisualVersion).where(VisualVersion.user_id == user_id, VisualVersion.visual_id == visual_id)
            )
        )
        paths = set()
        for version in versions:
            paths |= asset_paths(version.snapshot_json, user_id)
            paths |= asset_paths(version.body_json, user_id)
            paths |= {path for path in (version.media_path, version.poster_path, version.input_image_path) if path}
        if import_job is not None:
            paths |= asset_paths(import_job.result_json, user_id)
            paths |= asset_paths(import_job.context_json, user_id)
            # VisualImportJob 是 jobs 的 joined-table 子类；直接让 visual_id 的外键
            # 级联只会删除详情行，父任务和 job_assets 仍会留下。显式删除子类实例
            # 让 ORM 按 joined-table 生命周期一并删除共享任务父行。
            await db.delete(import_job)
            await db.flush()
        await db.delete(item)
        await db.flush()
        await enqueue_asset_cleanup(db, user_id, paths, expedite=True)
        await notify_visuals_updated(db, user_id)
        await db.commit()


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
        elif (job.state == "unknown" and (progress is None or progress.failure_reason != "result_download_failed")) or (
            progress is not None and visual_progress_has_unknown_submission(progress)
        ):
            raise VisualStateError("供应商提交结果尚未确认，请先核对结果或放弃本次制作")
        if progress is None:
            raise VisualStateError("制作草稿已失效")
        if progress.failure_reason == "activity_unsupported":
            raise VisualStateError("这个活动当前无法制作，请调整要求后重新发起制作")
        new_image = job.state == "failed" and (
            progress.failure_reason in {"image_unsuitable", "reference_unsuitable"}
            or bool(progress.image_only and visual_image_chain_exhausted(progress))
        )
        if new_image:
            if progress.failure_reason == "reference_unsuitable":
                progress.reference_alignment_chain_json = None
                progress.reference_alignment_source_hash = None
                progress.aligned_outfit_path = None
                progress.aligned_outfit_hash = None
            review = progress.opening_review
            reference = progress.pose_path if identity_score_passed(review) else None
            begin_opening_repair(
                job,
                progress,
                reason=progress.repair_reason or job.error or "重新准备满足活动要求的起始画面",
                reference_path=reference,
                new_budget=True,
            )
            require_new_generation_call(user_id)
            job.checkpoint = progress.model_dump(mode="json")
            job.state, job.stage, job.error = "running", "repairing", None
            await notify_visuals_updated(db, user_id)
            view = job_view(job)
            await restart_visual_steps(db, user_id, job)
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
    autonomous_enabled: bool | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
) -> VisualPresentationView:
    async with _lock(user_id), SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
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
