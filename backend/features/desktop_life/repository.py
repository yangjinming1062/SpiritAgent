"""桌面生活资产的状态、选择与播放回执；生成流程由应用层管理。"""

import json
from datetime import timedelta
from uuid import uuid4

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.assets.asset_store import client_asset_url, resolve_asset_reference
from infrastructure.text import ensure_utc, utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import lock_user_row
from features.assets.lifecycle import enqueue_asset_cleanup
from features.assets.references import asset_paths
from features.desktop_life.models import (
    DesktopVideoAction,
    DesktopVideoPlayback,
    DesktopVideoProposal,
    DesktopVideoSet,
    DesktopVideoState,
)
from features.desktop_life.policy import desktop_autonomous_block_reason
from features.desktop_life.prompts import DESKTOP_PRESETS
from features.desktop_life.schemas import (
    DesktopVideoActionResponse,
    DesktopVideoAsset,
    DesktopVideoCurrentResponse,
    DesktopVideoDesignRequest,
    DesktopVideoDraftResponse,
    DesktopVideoPlayCommand,
    DesktopVideoPreferences,
    DesktopVideoProgress,
    DesktopVideoProposalResponse,
    DesktopVideoReceipt,
    DesktopVideoSetResponse,
    DesktopVideoStateResponse,
)
from features.media.contracts import RemovedActionVideoTask


class DesktopVideoError(RuntimeError):
    """可展示的桌面生活错误。"""


class DesktopVideoNotFoundError(DesktopVideoError):
    pass


class DesktopVideoStateError(DesktopVideoError):
    pass


class DesktopVideoAspectRatioError(DesktopVideoStateError):
    def __init__(self, width: int, height: int, target_width: int, target_height: int, media_kind: str) -> None:
        super().__init__("上传内容与桌景画幅不同，请选择处理方式")
        self.detail = {
            "error": str(self),
            "reason": "aspect_ratio_mismatch",
            "source_width": width,
            "source_height": height,
            "target_width": target_width,
            "target_height": target_height,
            "media_kind": media_kind,
            "choices": ["keep", "expand"],
        }


def desktop_accepted_asset(action: DesktopVideoAction) -> DesktopVideoAsset | None:
    if not action.accepted_asset_json:
        return None
    asset = DesktopVideoAsset.model_validate_json(action.accepted_asset_json)
    return (
        asset
        if resolve_asset_reference(asset.video_path) is not None
        and resolve_asset_reference(asset.poster_path) is not None
        else None
    )


def desktop_progress(action: DesktopVideoAction) -> DesktopVideoProgress | None:
    return (
        DesktopVideoProgress.model_validate_json(action.generation_state_json) if action.generation_state_json else None
    )


async def desktop_image_origin(
    db: AsyncSession,
    user_id: int,
    set_id: int,
    image_path: str | None,
) -> GenerationOrigin | None:
    """来源跟随实际图片；共享基础图也从所属套装的成品或草稿读取。"""
    if not image_path:
        return None
    actions = await db.scalars(
        select(DesktopVideoAction).where(DesktopVideoAction.user_id == user_id, DesktopVideoAction.set_id == set_id),
    )
    for action in actions:
        progress = desktop_progress(action)
        if progress is not None:
            if progress.pose_path == image_path:
                return progress.image_origin
            if progress.previous_pose_path == image_path:
                return progress.previous_image_origin
            if progress.candidate is not None and progress.candidate.image_path == image_path:
                return progress.candidate.image_origin
        if action.accepted_asset_json:
            asset = DesktopVideoAsset.model_validate_json(action.accepted_asset_json)
            if asset.image_path == image_path:
                return asset.image_origin
    return None


def desktop_removed_video_task(action: DesktopVideoAction) -> RemovedActionVideoTask | None:
    progress = desktop_progress(action)
    if (
        progress is None
        or not progress.provider_task_id
        or progress.download_url
        or progress.source_path
        or progress.candidate
    ):
        return None
    return RemovedActionVideoTask(
        action_id=action.id,
        task_id=progress.provider_task_id,
        generation_state_json=progress.video_chain_json,
        provider="",
    )


async def desktop_state(
    db: AsyncSession,
    user_id: int,
    *,
    create: bool = False,
    lock: bool = False,
) -> DesktopVideoState | None:
    if create or lock:
        await lock_user_row(db, user_id)
    row = await db.scalar(select(DesktopVideoState).where(DesktopVideoState.user_id == user_id))
    if row is not None and (create or lock) and not db.is_modified(row):
        await db.refresh(row)
    if row is None and create:
        row = DesktopVideoState(user_id=user_id, version=0, set_epoch=0, pinned=False, autonomous_enabled=True)
        db.add(row)
        await db.flush()
    return row


async def desktop_action(db: AsyncSession, user_id: int, action_id: int, *, lock: bool = False) -> DesktopVideoAction:
    if lock:
        await desktop_state(db, user_id, create=True)
    action = await db.scalar(
        select(DesktopVideoAction)
        .where(DesktopVideoAction.id == action_id, DesktopVideoAction.user_id == user_id)
        .execution_options(populate_existing=True),
    )
    if action is None:
        raise DesktopVideoNotFoundError("找不到对应的桌面动作")
    return action


def desktop_action_description(action: DesktopVideoAction) -> str:
    """预设用途按当前定义读取，自定义动作保留已评审的设计。"""
    if action.preset:
        for preset in DESKTOP_PRESETS:
            if preset.key == action.key:
                return preset.description
    return action.description


def desktop_action_response(action: DesktopVideoAction) -> DesktopVideoActionResponse:
    accepted = desktop_accepted_asset(action)
    progress = desktop_progress(action)
    candidate = progress.candidate if progress is not None else None
    poster = accepted.poster_path if accepted else progress.pose_path if progress else ""
    playable = accepted is not None and action.enabled and not action.needs_update
    draft = progress if progress is not None and action.stage != "complete" else None
    video_review_pending = (
        action.status == "review_pending" and action.stage != "image_review" and candidate is not None
    )
    error = None if video_review_pending else action.error
    return DesktopVideoActionResponse(
        id=action.id,
        current=DesktopVideoCurrentResponse(
            video_url=client_asset_url(accepted.video_path),
            poster_url=client_asset_url(accepted.poster_path),
            image_url=client_asset_url(accepted.image_path),
            duration_ms=accepted.duration_ms,
            width=accepted.width,
            height=accepted.height,
            geometry_mode=accepted.geometry_mode,
            continuity=accepted.continuity,
            base_version=accepted.base_version,
            transition=accepted.transition,
        )
        if accepted
        else None,
        draft=DesktopVideoDraftResponse(
            id=draft.generation_id,
            stage=action.stage,
            source=draft.source,
            target_width=draft.target_width,
            target_height=draft.target_height,
            resolution_revision=draft.resolution_revision,
            geometry_mode=draft.geometry_mode,
            duration_seconds=draft.duration_seconds,
            video_refinement=draft.video_refinement is not None,
            frame_width=candidate.width if candidate else draft.frame_width,
            frame_height=candidate.height if candidate else draft.frame_height,
            image_url=client_asset_url(draft.pose_path or draft.previous_pose_path)
            if draft.pose_path or draft.previous_pose_path
            else "",
            image_confirmed=draft.image_confirmed,
            feedback=draft.feedback,
            submission_unknown=draft.submission_unknown,
            candidate_video_url=client_asset_url(candidate.video_path) if candidate else "",
            candidate_poster_url=client_asset_url(candidate.poster_path) if candidate else "",
            review_reason=(draft.review_reason or action.error or "") if video_review_pending else "",
            base_version=draft.base_version,
            error=error,
        )
        if draft
        else None,
        playable=playable,
        needs_update=action.needs_update,
        unavailable_reason="基础画面已更新，请更新这个动作"
        if action.needs_update
        else None
        if playable
        else "尚未制作完成"
        if accepted is None
        else "这个动作已停用",
        set_id=action.set_id,
        key=action.key,
        name=action.name,
        description=desktop_action_description(action),
        kind=action.kind,
        duration_seconds=action.duration_seconds,
        target_duration_seconds=action.target_duration_seconds,
        status="queued" if action.status == "ready" and accepted is None else action.status,
        stage=action.stage,
        error=error,
        enabled=action.enabled,
        preset=action.preset,
        use_when=json.loads(action.use_when_json),
        avoid_when=json.loads(action.avoid_when_json),
        allowed_triggers=json.loads(action.allowed_triggers_json),
        transition=accepted.transition if accepted else "fade",
        continuity=accepted.continuity if accepted else action.continuity,
        base_version=accepted.base_version if accepted else None,
        version=action.version,
        poster_url=client_asset_url(poster) if poster else "",
        video_url=client_asset_url(accepted.video_path) if accepted else "",
        actual_duration_ms=accepted.duration_ms if accepted else None,
    )


async def desktop_set_response(
    db: AsyncSession,
    row: DesktopVideoSet,
    *,
    current_id: int | None,
) -> DesktopVideoSetResponse:
    actions = list(
        await db.scalars(
            select(DesktopVideoAction)
            .where(DesktopVideoAction.set_id == row.id, DesktopVideoAction.user_id == row.user_id)
            .order_by(DesktopVideoAction.id),
        ),
    )
    proposals = list(
        await db.scalars(
            select(DesktopVideoProposal)
            .where(DesktopVideoProposal.set_id == row.id, DesktopVideoProposal.user_id == row.user_id)
            .order_by(DesktopVideoProposal.updated_at.desc())
            .limit(25),
        ),
    )
    proposal_responses = []
    for proposal in proposals:
        design = DesktopVideoDesignRequest.model_validate_json(proposal.design_json)
        proposal_responses.append(
            DesktopVideoProposalResponse(
                proposal_id=proposal.id,
                set_id=row.id,
                action_id=proposal.action_id,
                status=proposal.status,
                review_reason=proposal.review_reason,
                name=design.name,
                description=design.motion_description,
                created_at=proposal.created_at,
            ),
        )
    return DesktopVideoSetResponse(
        id=row.id,
        title=row.title,
        target_width=row.target_width,
        target_height=row.target_height,
        resolution_revision=row.resolution_revision,
        is_default=row.is_default,
        source=row.source,
        outfit_id=row.outfit_id,
        scene_id=row.scene_id,
        status=row.status,
        created_at=row.created_at,
        is_current=row.id == current_id,
        actions=[desktop_action_response(action) for action in actions],
        proposals=proposal_responses,
        base_image_url=client_asset_url(row.base_image_path) if row.base_image_path else "",
        base_version=row.base_version,
    )


async def desktop_state_response(
    db: AsyncSession,
    user_id: int,
    *,
    preparation_error: str | None = None,
) -> DesktopVideoStateResponse:
    state = await desktop_state(db, user_id)
    if state is None:
        return DesktopVideoStateResponse(preparation_error=preparation_error)
    row = await db.get(DesktopVideoSet, state.current_set_id) if state.current_set_id else None
    current = (
        await desktop_set_response(db, row, current_id=state.current_set_id)
        if row is not None and row.user_id == user_id
        else None
    )
    return DesktopVideoStateResponse(
        current=current,
        selected_action_id=state.selected_action_id,
        loop_action_id=state.loop_action_id,
        set_epoch=state.set_epoch,
        pinned=state.pinned,
        autonomous_enabled=state.autonomous_enabled,
        version=state.version,
        preparation_error=preparation_error or state.preparation_error,
    )


async def touch_desktop_state(db: AsyncSession, user_id: int) -> DesktopVideoState:
    state = await desktop_state(db, user_id, create=True)
    state.version += 1
    emit_ws_event(db, user_id=user_id, event_type="companion.desktop_video.updated", payload={"version": state.version})
    return state


async def desktop_preferences(db: AsyncSession, user_id: int, request: DesktopVideoPreferences) -> None:
    state = await desktop_state(db, user_id, create=True)
    if request.pinned is not None:
        state.pinned = request.pinned
    if request.autonomous_enabled is not None:
        state.autonomous_enabled = request.autonomous_enabled
    await touch_desktop_state(db, user_id)


async def require_desktop_current(db: AsyncSession, user_id: int, set_id: int) -> DesktopVideoState:
    state = await desktop_state(db, user_id, lock=True)
    if state is None or state.current_set_id != set_id:
        raise DesktopVideoStateError("当前画面已切换，请重新获取可用动作")
    return state


async def emit_desktop_play(
    db: AsyncSession,
    user_id: int,
    action: DesktopVideoAction,
    *,
    source: str,
    presentation_revision: int,
) -> DesktopVideoPlayCommand:
    state = await require_desktop_current(db, user_id, action.set_id)
    asset = desktop_accepted_asset(action)
    if not action.enabled or action.needs_update or asset is None:
        raise DesktopVideoStateError("这个桌面动作尚未就绪")
    if source == "autonomous" and (state.pinned or not state.autonomous_enabled):
        raise DesktopVideoStateError("桌面动作已固定或自主切换已关闭")
    if source == "autonomous":
        blocked = await desktop_autonomous_block_reason(
            db,
            state,
            action,
            asset,
            presentation_revision=presentation_revision,
        )
        if blocked is not None:
            raise DesktopVideoStateError(f"当前不适合自主切换桌面动作：{blocked}")
    playback = DesktopVideoPlayback(
        user_id=user_id,
        set_id=action.set_id,
        action_id=action.id,
        play_id=str(uuid4()),
        set_epoch=state.set_epoch,
        presentation_revision=presentation_revision,
        status="queued",
        source=source,
        asset_version=action.version,
        expires_at=utc_now() + timedelta(seconds=30),
    )
    db.add(playback)
    current_set = await db.get(DesktopVideoSet, action.set_id)
    matches_base = current_set is not None and asset.base_version == current_set.base_version
    command = DesktopVideoPlayCommand(
        play_id=playback.play_id,
        set_id=action.set_id,
        action_id=action.id,
        set_epoch=state.set_epoch,
        kind=action.kind,
        expires_at=playback.expires_at,
        video_url=client_asset_url(asset.video_path),
        poster_url=client_asset_url(asset.poster_path),
        geometry_mode=asset.geometry_mode,
        version=action.version,
        transition=asset.transition if matches_base else "fade",
        continuity=asset.continuity if matches_base else "independent",
        base_version=asset.base_version if matches_base else None,
    )
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.desktop_video.play_requested",
        payload=command.model_dump(mode="json"),
    )
    return command


async def fulfill_desktop_play_intents(
    db: AsyncSession,
    action: DesktopVideoAction,
    *,
    presentation_revision: int,
    allow_autonomous: bool,
) -> bool:
    state = await desktop_state(db, action.user_id, lock=True)
    asset = desktop_accepted_asset(action)
    if state is None or asset is None or action.needs_update:
        return False
    emitted = False
    plays = list(
        await db.scalars(
            select(DesktopVideoPlayback)
            .where(
                DesktopVideoPlayback.user_id == action.user_id,
                DesktopVideoPlayback.action_id == action.id,
                DesktopVideoPlayback.status == "waiting",
            )
            .order_by(DesktopVideoPlayback.id),
        ),
    )
    for play in plays:
        if ensure_utc(play.expires_at) <= utc_now():
            continue
        if (
            play.set_id != state.current_set_id
            or play.set_epoch != state.set_epoch
            or play.presentation_revision != presentation_revision
        ):
            play.status = "rejected"
            continue
        if play.source in {"autonomous", "chat_expression"} and (
            state.pinned or not state.autonomous_enabled or (play.source == "autonomous" and not allow_autonomous)
        ):
            play.status = "rejected"
            continue
        if (
            play.source == "autonomous"
            and await desktop_autonomous_block_reason(
                db,
                state,
                action,
                asset,
                presentation_revision=presentation_revision,
                excluded_play_id=play.play_id,
            )
            is not None
        ):
            play.status = "rejected"
            continue
        play.status, play.expires_at = "queued", utc_now() + timedelta(seconds=30)
        play.asset_version = action.version
        current_set = await db.get(DesktopVideoSet, action.set_id)
        matches_base = current_set is not None and asset.base_version == current_set.base_version
        command = DesktopVideoPlayCommand(
            play_id=play.play_id,
            set_id=action.set_id,
            action_id=action.id,
            set_epoch=state.set_epoch,
            kind=action.kind,
            expires_at=play.expires_at,
            video_url=client_asset_url(asset.video_path),
            poster_url=client_asset_url(asset.poster_path),
            geometry_mode=asset.geometry_mode,
            version=action.version,
            transition=asset.transition if matches_base else "fade",
            continuity=asset.continuity if matches_base else "independent",
            base_version=asset.base_version if matches_base else None,
        )
        emit_ws_event(
            db,
            user_id=action.user_id,
            event_type="companion.desktop_video.play_requested",
            payload=command.model_dump(mode="json"),
        )
        emitted = True
    return emitted


async def claim_desktop_play(
    db: AsyncSession,
    user_id: int,
    play_id: str,
    client_id: str,
    *,
    presentation_revision: int,
    allow_autonomous: bool,
) -> bool:
    state = await desktop_state(db, user_id, lock=True)
    play = await db.scalar(
        select(DesktopVideoPlayback)
        .where(DesktopVideoPlayback.user_id == user_id, DesktopVideoPlayback.play_id == play_id)
        .with_for_update()
        .execution_options(populate_existing=True),
    )
    if play is None or ensure_utc(play.expires_at) <= utc_now() or play.status not in {"queued", "claimed"}:
        return False
    if (
        state is None
        or state.current_set_id != play.set_id
        or state.set_epoch != play.set_epoch
        or play.presentation_revision != presentation_revision
    ):
        play.status = "rejected"
        return False
    if play.client_id is not None and play.client_id != client_id:
        return False
    action = await desktop_action(db, user_id, play.action_id)
    asset = desktop_accepted_asset(action)
    if not action.enabled or action.needs_update or asset is None or play.asset_version != action.version:
        play.status = "rejected"
        return False
    if play.source == "autonomous" and (
        not allow_autonomous
        or await desktop_autonomous_block_reason(
            db,
            state,
            action,
            asset,
            presentation_revision=presentation_revision,
            excluded_play_id=play.play_id,
        )
        is not None
    ):
        play.status = "rejected"
        return False
    latest_started = await db.scalar(
        select(DesktopVideoPlayback)
        .where(
            DesktopVideoPlayback.user_id == user_id,
            DesktopVideoPlayback.set_id == state.current_set_id,
            DesktopVideoPlayback.set_epoch == state.set_epoch,
            DesktopVideoPlayback.started_at.is_not(None),
        )
        .order_by(DesktopVideoPlayback.id.desc())
        .limit(1),
    )
    if latest_started is not None and latest_started.id > play.id:
        play.status = "rejected"
        return False
    play.client_id = client_id
    play.status = "claimed"
    return True


async def record_desktop_receipt(
    db: AsyncSession,
    user_id: int,
    play_id: str,
    receipt: DesktopVideoReceipt,
    *,
    presentation_revision: int = -1,
) -> None:
    state = await desktop_state(db, user_id, lock=True)
    play = await db.scalar(
        select(DesktopVideoPlayback)
        .where(DesktopVideoPlayback.user_id == user_id, DesktopVideoPlayback.play_id == play_id)
        .with_for_update()
        .execution_options(populate_existing=True),
    )
    if play is None:
        raise DesktopVideoNotFoundError("找不到对应的桌面播放")
    if play.client_id != receipt.client_id:
        raise DesktopVideoStateError("这个播放由其他设备处理")
    if play.status in {"completed", "interrupted", "failed", "rejected"}:
        return
    current = state is not None and state.current_set_id == play.set_id and state.set_epoch == play.set_epoch
    if receipt.status == "started":
        if play.status == "started":
            return
        play.status = "started"
        play.started_at = utc_now()
        if not current or play.presentation_revision != presentation_revision:
            return
        latest_started = await db.scalar(
            select(DesktopVideoPlayback)
            .where(
                DesktopVideoPlayback.user_id == user_id,
                DesktopVideoPlayback.set_id == state.current_set_id,
                DesktopVideoPlayback.set_epoch == state.set_epoch,
                DesktopVideoPlayback.started_at.is_not(None),
            )
            .order_by(DesktopVideoPlayback.id.desc())
            .limit(1),
        )
        if latest_started is not None and latest_started.id > play.id:
            return
        action = await desktop_action(db, user_id, play.action_id)
        state.selected_play_id = play.play_id
        state.selected_action_id = action.id
        if action.kind == "loop":
            prior_loop_start = await db.scalar(
                select(DesktopVideoPlayback.id)
                .where(
                    DesktopVideoPlayback.user_id == user_id,
                    DesktopVideoPlayback.set_id == state.current_set_id,
                    DesktopVideoPlayback.set_epoch == state.set_epoch,
                    DesktopVideoPlayback.action_id == action.id,
                    DesktopVideoPlayback.started_at.is_not(None),
                    DesktopVideoPlayback.play_id != play.play_id,
                )
                .limit(1),
            )
            first_loop_start = prior_loop_start is None
            if first_loop_start or state.loop_action_id != action.id or state.loop_changed_at is None:
                state.loop_changed_at = play.started_at
            state.loop_action_id = action.id
        await touch_desktop_state(db, user_id)
        return
    if receipt.status == "completed":
        if play.status != "started":
            raise DesktopVideoStateError("播放尚未开始，不能记为完成")
        action = await desktop_action(db, user_id, play.action_id)
        if action.kind == "loop":
            raise DesktopVideoStateError("持续动作没有自然完成，请报告播放中断")
    play.status = receipt.status
    play.error = receipt.error
    if (
        current
        and play.presentation_revision == presentation_revision
        and state.selected_play_id == play.play_id
        and receipt.status in {"completed", "interrupted", "failed"}
    ):
        action = await desktop_action(db, user_id, play.action_id)
        if action.kind == "once":
            state.selected_action_id = state.loop_action_id
            state.selected_play_id = None
            await touch_desktop_state(db, user_id)


async def publish_desktop_asset(
    db: AsyncSession,
    action: DesktopVideoAction,
    asset: DesktopVideoAsset,
) -> list[RemovedActionVideoTask]:
    if resolve_asset_reference(asset.video_path) is None or resolve_asset_reference(asset.poster_path) is None:
        raise DesktopVideoStateError("视频或封面缺失，请重新制作这个动作")
    await desktop_state(db, action.user_id, lock=True)
    previous_paths = asset_paths(action.accepted_asset_json, action.user_id)
    removed_tasks: list[RemovedActionVideoTask] = []
    progress = desktop_progress(action)
    generation_paths = asset_paths(action.generation_state_json, action.user_id)
    row = await db.get(DesktopVideoSet, action.set_id)
    if row is None or row.user_id != action.user_id:
        raise DesktopVideoStateError("当前画面已被移除")
    if (
        action.continuity == "base"
        and action.key != "idle"
        and (asset.base_version != row.base_version or asset.base_image_path != row.base_image_path)
    ):
        raise DesktopVideoStateError("基础画面已变化，请重新制作这个动作")
    base_changed = False
    if action.key == "idle":
        image_path = asset.image_path
        if not image_path or resolve_asset_reference(image_path) is None:
            raise DesktopVideoStateError("基础图片缺失，请重新制作安静相伴")
        proposed = asset.base_version or row.base_version + 1
        if proposed not in {row.base_version, row.base_version + 1}:
            raise DesktopVideoStateError("基础画面已变化，请重新制作安静相伴")
        if proposed == row.base_version and image_path != row.base_image_path:
            raise DesktopVideoStateError("基础画面已变化，请重新制作安静相伴")
        base_changed = proposed != row.base_version or image_path != row.base_image_path
        row.base_image_path, row.base_version = image_path, proposed
        asset = asset.model_copy(update={"continuity": "base", "base_image_path": image_path, "base_version": proposed})
        if base_changed:
            dependents = list(
                await db.scalars(
                    select(DesktopVideoAction).where(
                        DesktopVideoAction.set_id == row.id,
                        DesktopVideoAction.id != action.id,
                        DesktopVideoAction.continuity == "base",
                    ),
                ),
            )
            for dependent in dependents:
                removed_task = desktop_removed_video_task(dependent)
                if removed_task is not None:
                    removed_tasks.append(removed_task)
                pending = desktop_progress(dependent)
                if pending is not None:
                    await enqueue_asset_cleanup(
                        db,
                        action.user_id,
                        asset_paths(dependent.generation_state_json, action.user_id),
                    )
                dependent.generation_state_json = None
                dependent.needs_update = desktop_accepted_asset(dependent) is not None
                dependent.update_pending = dependent.needs_update and dependent.enabled
                dependent.status = "ready" if dependent.accepted_asset_json else "queued"
                dependent.stage = "needs_update" if dependent.needs_update else "prepare"
                dependent.error = None
            state = await desktop_state(db, action.user_id)
            if state is not None and state.current_set_id == row.id:
                state.set_epoch += 1
                state.selected_action_id = state.loop_action_id = action.id
                state.selected_play_id = None
                state.loop_changed_at = utc_now()
    if base_changed or previous_paths:
        query = select(DesktopVideoPlayback).where(
            DesktopVideoPlayback.set_id == row.id,
            DesktopVideoPlayback.status.in_(("waiting", "queued", "claimed", "started")),
        )
        if not base_changed:
            query = query.where(DesktopVideoPlayback.action_id == action.id)
        for play in await db.scalars(query):
            play.status = "rejected"
        state = await desktop_state(db, action.user_id)
        if (
            not base_changed
            and state is not None
            and state.current_set_id == row.id
            and state.selected_action_id == action.id
        ):
            state.selected_play_id = None
            if action.kind == "once":
                state.selected_action_id = state.loop_action_id
    action.needs_update = action.update_pending = False
    if progress is not None:
        completed = DesktopVideoProgress(
            generation_id=progress.generation_id,
            source=progress.source,
            target_width=progress.target_width,
            target_height=progress.target_height,
            resolution_revision=progress.resolution_revision,
            geometry_mode=progress.geometry_mode,
            source_image_path=progress.source_image_path,
            video_refinement=progress.video_refinement,
            duration_seconds=progress.duration_seconds,
            image_confirmed=True,
            pose_path=asset.image_path,
            image_origin=asset.image_origin,
            continuity=asset.continuity,
            base_image_path=asset.base_image_path,
            base_version=asset.base_version,
            aspect_ratio=progress.aspect_ratio,
            frame_width=asset.width,
            frame_height=asset.height,
            visual_description=asset.visual_description,
        )
        action.generation_state_json = completed.model_dump_json()
    current_paths = {
        asset.video_path,
        asset.poster_path,
        asset.image_path,
        *([asset.source_image_path] if asset.source_image_path else []),
        *([asset.source_video_path] if asset.source_video_path else []),
        *([asset.base_image_path] if asset.base_image_path else []),
    }
    if progress is not None:
        current_paths |= asset_paths(action.generation_state_json, action.user_id)
    await enqueue_asset_cleanup(
        db,
        action.user_id,
        (generation_paths | previous_paths) - current_paths,
        not_before=utc_now() + timedelta(hours=24),
    )
    action.accepted_asset_json = asset.model_dump_json()
    action.version += 1
    action.status, action.stage, action.error = "ready", "complete", None
    if row is not None and action.key == "idle":
        row.status = "ready"
    state = await touch_desktop_state(db, action.user_id)
    if state.current_set_id == action.set_id and state.selected_action_id is None and action.key == "idle":
        state.selected_action_id = state.loop_action_id = action.id
        state.loop_changed_at = utc_now()
    return removed_tasks
