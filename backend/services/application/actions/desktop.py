"""桌面生活的组合准备、动作评审与播放协调。"""

import asyncio
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from components import (
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    parse_llm_json,
    resolve_language,
    resolve_prompt_text,
    track_user_task,
    utc_now,
)
from modules.companion import (
    CompanionScene,
    DesktopVideoAction,
    DesktopVideoActionResponse,
    DesktopVideoDesignRequest,
    DesktopVideoExternalPromptResponse,
    DesktopVideoListResponse,
    DesktopVideoPlayback,
    DesktopVideoPlayCommand,
    DesktopVideoPlayRequest,
    DesktopVideoPreferences,
    DesktopVideoProgress,
    DesktopVideoProposal,
    DesktopVideoProposalResponse,
    DesktopVideoReceipt,
    DesktopVideoReference,
    DesktopVideoSet,
    DesktopVideoStateResponse,
    DesktopVisualSnapshot,
)
from modules.settings import get_user_setting
from prompts.desktop_videos import (
    DESKTOP_ACTION_REVIEW,
    DESKTOP_CONTEXT_GUIDANCE,
    DESKTOP_IDLE_EXPRESSION,
    DESKTOP_PRESETS,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.generation import (
    desktop_context_hash,
    desktop_progress_can_resume,
    load_desktop_visual_snapshot,
    require_new_generation_call,
    resolve_desktop_video_plan,
    schedule_desktop_video_job,
)
from services.domains.actions import (
    DEFERRED_PLAY_INTENT_TTL_SECONDS,
    ActionPolicyError,
    DesktopVideoError,
    DesktopVideoNotFoundError,
    DesktopVideoStateError,
    claim_desktop_play,
    consume_create_slot,
    desktop_accepted_asset,
    desktop_action,
    desktop_action_response,
    desktop_preferences,
    desktop_progress,
    desktop_set_response,
    desktop_state,
    desktop_state_response,
    emit_desktop_play,
    fulfill_desktop_play_intents,
    normalize_action_name,
    publish_desktop_asset,
    record_desktop_receipt,
    require_desktop_current,
    touch_desktop_state,
)
from services.domains.assets import asset_paths, enqueue_asset_cleanup
from services.domains.companion import (
    get_disturbance_tier,
    get_presentation_snapshot,
    get_user_proactive_record,
    load_companion_prompt_context,
)
from services.domains.conversation import load_recent_context_window
from services.infrastructure.assets import (
    read_asset_data_uri,
    save_action_source_asset_async,
    signed_companion_asset_url,
    sniff_media_ext,
    unlink_companion_asset,
)
from services.infrastructure.llm import LlmCallBlockedError, VisualReasoningError, chat, vision_chat

logger = get_logger(__name__)
_LOCKS: dict[int, asyncio.Lock] = {}
_REVIEWS: dict[int, asyncio.Task[None]] = {}


def _lock(user_id: int) -> asyncio.Lock:
    return _LOCKS.setdefault(user_id, asyncio.Lock())


def _require_desktop_mode(user_id: int) -> None:
    presentation = get_presentation_snapshot(user_id)
    if presentation is None or presentation.mode != "desktop":
        raise DesktopVideoStateError("桌面模式当前未启用")


async def _wait_desktop_mode(user_id: int) -> None:
    for _attempt in range(20):
        presentation = get_presentation_snapshot(user_id)
        if presentation is not None and presentation.mode == "desktop":
            return
        await asyncio.sleep(0.1)
    _require_desktop_mode(user_id)


async def _desired_context(db: AsyncSession, user_id: int) -> tuple[str | None, str | None]:
    try:
        snapshot = await load_desktop_visual_snapshot(db, user_id)
        return desktop_context_hash(snapshot), None
    except (DesktopVideoError, VisualReasoningError, OSError) as exc:
        return None, str(exc) if isinstance(exc, (DesktopVideoError, VisualReasoningError)) else "参考图片暂时无法读取"


async def get_desktop_video_state(db: AsyncSession, user_id: int) -> DesktopVideoStateResponse:
    desired, error = await _desired_context(db, user_id)
    return await desktop_state_response(db, user_id, desired_hash=desired, preparation_error=error)


async def list_desktop_video_sets(db: AsyncSession, user_id: int) -> DesktopVideoListResponse:
    state = await desktop_state(db, user_id)
    rows = list(
        await db.scalars(
            select(DesktopVideoSet)
            .where(DesktopVideoSet.user_id == user_id)
            .order_by(DesktopVideoSet.updated_at.desc()),
        ),
    )
    return DesktopVideoListResponse(
        sets=[await desktop_set_response(db, row, current_id=state.current_set_id if state else None) for row in rows],
        version=state.version if state else 0,
    )


async def get_desktop_action_response(db: AsyncSession, user_id: int, action_id: int) -> DesktopVideoActionResponse:
    return desktop_action_response(await desktop_action(db, user_id, action_id))


async def get_desktop_external_prompt(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    *,
    expected_set_id: int | None = None,
    requirements: str = "",
) -> DesktopVideoExternalPromptResponse:
    action = await desktop_action(db, user_id, action_id)
    if expected_set_id is not None and expected_set_id != action.set_id:
        raise DesktopVideoStateError("桌面生活组合已经变化")
    await require_desktop_current(db, user_id, action.set_id)
    snapshot = await load_desktop_visual_snapshot(db, user_id)
    row = await db.get(DesktopVideoSet, action.set_id)
    if row is None or row.context_hash != desktop_context_hash(snapshot):
        raise DesktopVideoStateError("请先准备当前穿着和场景的桌面组合")
    scene = await db.scalar(
        select(CompanionScene).where(
            CompanionScene.id == snapshot.scene_id,
            CompanionScene.user_id == user_id,
        ),
    )
    references: list[DesktopVideoReference] = []
    for label, path in (
        ("身份参考图", snapshot.identity_path),
        ("当前穿着参考图", snapshot.outfit_path),
        ("桌景参考图", scene.media_path if scene is not None else ""),
    ):
        url = signed_companion_asset_url(path) if path else None
        if url:
            references.append(DesktopVideoReference(label=label, url=url))
    language = resolve_language(await get_user_setting(db, user_id, "language"))
    extra = requirements.strip()
    if language == "en":
        prompt = (
            f"Create a {action.duration_seconds}-second {action.kind} video of the same character in the supplied "
            f"current outfit and desktop scene. Keep the identity, body structure, outfit, scene, lighting and "
            f"camera stable; show only this action: {action.description}. Keep motion physically natural and "
            f"suitable for the character. Do not add people, subtitles, logos, watermarks, cuts or split screens. "
            f"Do not change the framing unless needed for the action."
        )
        if extra:
            prompt += f" Additional user requirements: {extra}"
    else:
        prompt = (
            f"制作一段 {action.duration_seconds} 秒的{('循环' if action.kind == 'loop' else '单次')}视频，使用所提供的同一伙伴、当前穿着和桌景。"
            f"保持身份、身体结构、穿着、桌景、光线和镜头稳定，只表现以下动作：{action.description}"
            "。动作要符合身体结构，受力、接触和衣物变化自然。不要新增人物，不要字幕、标志、水印、切镜或分屏；"
            "不要改变取景，除非动作确实需要。"
        )
        if extra:
            prompt += f" 用户补充要求：{extra}"
    return DesktopVideoExternalPromptResponse(
        action_id=action.id,
        set_id=action.set_id,
        name=action.name,
        description=action.description,
        kind=action.kind,
        duration_seconds=action.duration_seconds,
        prompt=prompt,
        references=references,
    )


async def get_desktop_proposal_response(
    db: AsyncSession,
    user_id: int,
    proposal_id: int,
) -> DesktopVideoProposalResponse:
    row = await db.scalar(
        select(DesktopVideoProposal).where(
            DesktopVideoProposal.id == proposal_id,
            DesktopVideoProposal.user_id == user_id,
        ),
    )
    if row is None:
        raise DesktopVideoNotFoundError("找不到对应的桌面动作提案")
    return DesktopVideoProposalResponse(
        proposal_id=row.id,
        set_id=row.set_id,
        action_id=row.action_id,
        status=row.status,
        review_reason=row.review_reason,
    )


async def ensure_current_desktop_videos(user_id: int, *, source: str = "user_requested") -> DesktopVideoStateResponse:
    await _wait_desktop_mode(user_id)
    idle_id: int | None = None
    async with _lock(user_id), SESSION_LOCAL() as db:
        _require_desktop_mode(user_id)
        state = await desktop_state(db, user_id, create=True)
        try:
            snapshot = await load_desktop_visual_snapshot(db, user_id)
        except (DesktopVideoError, VisualReasoningError, OSError):
            return await get_desktop_video_state(db, user_id)
        signature = desktop_context_hash(snapshot)
        row = await db.scalar(
            select(DesktopVideoSet).where(
                DesktopVideoSet.user_id == user_id,
                DesktopVideoSet.context_hash == signature,
            ),
        )
        if row is None:
            row = DesktopVideoSet(
                user_id=user_id,
                avatar_id=snapshot.identity.avatar_id,
                outfit_id=snapshot.outfit_id,
                scene_id=snapshot.scene_id,
                context_hash=signature,
                context_json=snapshot.model_dump_json(),
                title=f"{snapshot.persona.get('name', '伙伴')} · {snapshot.scene_description[:60]}",
                status="preparing",
            )
            db.add(row)
            await db.flush()
            for key, name, description, kind, duration in DESKTOP_PRESETS:
                db.add(
                    DesktopVideoAction(
                        user_id=user_id,
                        set_id=row.id,
                        key=key,
                        name=name,
                        description=description,
                        kind=kind,
                        duration_seconds=duration,
                        preset=True,
                        status="queued",
                        stage="prepare",
                        enabled=True,
                        version=0,
                        attempt=0,
                    ),
                )
            await db.flush()
        if state.current_set_id != row.id:
            state.current_set_id = row.id
            state.set_epoch += 1
            state.selected_action_id = None
            state.selected_play_id = None
            state.loop_action_id = None
            state.preparation_error = None
            await touch_desktop_state(db, user_id)
        idle = await db.scalar(
            select(DesktopVideoAction).where(DesktopVideoAction.set_id == row.id, DesktopVideoAction.key == "idle"),
        )
        if idle is not None and desktop_accepted_asset(idle) is not None:
            if state.selected_action_id is None:
                state.selected_action_id = state.loop_action_id = idle.id
                await touch_desktop_state(db, user_id)
        elif idle is not None and idle.status in {"queued", "ready"}:
            idle_id = idle.id
        await db.commit()
    if idle_id is not None:
        try:
            await generate_desktop_action(user_id, idle_id, source=source)
        except DesktopVideoError as exc:
            async with _lock(user_id), SESSION_LOCAL() as db:
                state = await desktop_state(db, user_id)
                if state is not None:
                    state.preparation_error = str(exc)
                    await touch_desktop_state(db, user_id)
                    await db.commit()
    async with SESSION_LOCAL() as db:
        return await get_desktop_video_state(db, user_id)


def _resumable(progress: DesktopVideoProgress | None, stage: str, feedback: str) -> bool:
    """带反馈的重做不做恢复；临时中断且已有可续进度时沿原任务继续，不重复计额。"""
    return (
        not feedback
        and progress is not None
        and not progress.submission_unknown
        and (stage == "paused" or progress.failure_reason == "temporary" and desktop_progress_can_resume(progress))
    )


async def generate_desktop_action(
    user_id: int,
    action_id: int,
    *,
    feedback: str = "",
    source: str = "user_requested",
) -> DesktopVideoActionResponse:
    async with SESSION_LOCAL() as db:
        initial = await desktop_action(db, user_id, action_id)
        kind, duration = initial.kind, initial.duration_seconds
        if initial.status == "processing" and initial.stage != "paused":
            return desktop_action_response(initial)
        recoverable = _resumable(desktop_progress(initial), initial.stage, feedback)
    seconds, providers = (
        (duration, [])
        if recoverable
        else await resolve_desktop_video_plan(user_id, kind=kind, duration_seconds=duration)
    )
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id)
        state = await require_desktop_current(db, user_id, action.set_id)
        snapshot = await load_desktop_visual_snapshot(db, user_id)
        row = await db.get(DesktopVideoSet, action.set_id)
        if row is None or row.context_hash != desktop_context_hash(snapshot):
            raise DesktopVideoStateError("穿着或场景已经变化，请先准备当前组合")
        if source == "autonomous" and (
            (not state.autonomous_enabled and action.key != "idle") or not SETTINGS.action_autocreate_enabled
        ):
            raise DesktopVideoStateError("自主制作桌面动作当前已关闭")
        if action.status == "processing" and action.stage != "paused":
            return desktop_action_response(action)
        progress = desktop_progress(action)
        resume = _resumable(progress, action.stage, feedback)
        if not resume:
            require_new_generation_call(user_id)
            action.attempt += 1
            try:
                await consume_create_slot(
                    db,
                    user_id,
                    source=source,
                    creation_key=f"desktop:{action.id}:{action.attempt}",
                )
            except ActionPolicyError as exc:
                raise DesktopVideoStateError(str(exc)) from exc
            from_chain = {"providers": [provider.model_dump() for provider in providers]}
            old_paths = asset_paths(action.generation_state_json, user_id)
            progress = DesktopVideoProgress(
                generation_id=uuid4().hex,
                source=source,
                feedback=feedback,
                duration_seconds=seconds,
                video_chain_json=json.dumps(from_chain),
            )
            await enqueue_asset_cleanup(db, user_id, old_paths)
        else:
            progress.failure_reason = ""
        action.duration_seconds = progress.duration_seconds
        action.generation_state_json = progress.model_dump_json()
        action.status, action.stage, action.error = "processing", "prepare", None
        state.preparation_error = None
        await touch_desktop_state(db, user_id)
        await db.commit()
        response = desktop_action_response(action)
    schedule_desktop_video_job(user_id, action_id)
    return response


async def upload_desktop_action(
    user_id: int,
    action_id: int,
    data: bytes,
    extension: str,
    *,
    expected_set_id: int | None = None,
) -> DesktopVideoActionResponse:
    """受理用户制作的视频；保存原文件后走无转码校验和直接发布路径。"""
    if not data:
        raise DesktopVideoStateError("上传的视频文件为空")
    extension = extension.lower().lstrip(".")
    detected = sniff_media_ext(data)
    if extension not in {"mp4", "mov"} or detected not in {"mp4", "mov"}:
        raise DesktopVideoStateError("仅支持 MP4 或 MOV 视频")
    async with _lock(user_id), SESSION_LOCAL() as db:
        initial = await desktop_action(db, user_id, action_id)
        if expected_set_id is not None and expected_set_id != initial.set_id:
            raise DesktopVideoStateError("桌面生活组合已经变化")
        if initial.status == "processing":
            raise DesktopVideoStateError("这个桌面动作正在制作，请等待当前任务完成")
        await require_desktop_current(db, user_id, initial.set_id)
        snapshot = await load_desktop_visual_snapshot(db, user_id)
        row = await db.get(DesktopVideoSet, initial.set_id)
        if row is None or row.context_hash != desktop_context_hash(snapshot):
            raise DesktopVideoStateError("穿着或场景已经变化，请先准备当前组合")
        initial.attempt += 1
        generation_id = uuid4().hex
        directory = f"desktop/{initial.set_id}/{initial.id}/{generation_id}"
        old_paths = asset_paths(initial.generation_state_json, user_id)
        previous = desktop_accepted_asset(initial)
        if previous is not None:
            old_paths -= {previous.video_path, previous.poster_path}
        progress = DesktopVideoProgress(
            generation_id=generation_id,
            source="external_upload",
            duration_seconds=initial.duration_seconds,
            video_chain_json="{}",
        )
        await enqueue_asset_cleanup(db, user_id, old_paths)
        initial.generation_state_json = progress.model_dump_json()
        initial.status, initial.stage, initial.error = "processing", "upload", None
        state = await desktop_state(db, user_id)
        if state is not None:
            state.preparation_error = None
        await touch_desktop_state(db, user_id)
        await db.commit()
    source_path: str | None = None
    try:
        source_path = await save_action_source_asset_async(
            data,
            user_id=user_id,
            generation_id=generation_id,
            attempt=initial.attempt,
            ext=detected,
            directory=directory,
        )
        async with SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id)
            current = desktop_progress(action)
            if current is None or current.generation_id != generation_id or action.status != "processing":
                raise DesktopVideoStateError("上传任务已经失效")
            current.source_path = source_path
            action.generation_state_json = current.model_dump_json()
            action.stage = "process"
            await touch_desktop_state(db, user_id)
            await db.commit()
            response = desktop_action_response(action)
    except Exception as exc:
        if source_path:
            await asyncio.to_thread(unlink_companion_asset, source_path)
        async with SESSION_LOCAL() as db:
            action = await db.scalar(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.id == action_id,
                    DesktopVideoAction.user_id == user_id,
                ),
            )
            current = desktop_progress(action) if action else None
            if action is not None and current is not None and current.generation_id == generation_id:
                current.failure_reason = "failed"
                action.generation_state_json = current.model_dump_json()
                action.status, action.stage, action.error = "failed", "upload", "上传视频保存失败，请重试"
                await touch_desktop_state(db, user_id)
                await db.commit()
        if isinstance(exc, DesktopVideoError):
            raise
        raise DesktopVideoStateError("上传视频保存失败，请重试") from exc
    schedule_desktop_video_job(user_id, action_id)
    return response


async def review_desktop_action(user_id: int, action_id: int, *, accepted: bool) -> DesktopVideoActionResponse:
    async with _lock(user_id), SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id)
        if action.status != "review_pending":
            raise DesktopVideoStateError("这个桌面动作当前没有待确认成品")
        progress = desktop_progress(action)
        if progress is None or progress.candidate is None:
            raise DesktopVideoStateError("待确认成品缺失，请重做")
        if accepted:
            await require_desktop_current(db, user_id, action.set_id)
            snapshot = await load_desktop_visual_snapshot(db, user_id)
            row = await db.get(DesktopVideoSet, action.set_id)
            if row is None or row.context_hash != desktop_context_hash(snapshot):
                raise DesktopVideoStateError("穿着或场景已经变化，无法采纳旧组合")
            await publish_desktop_asset(db, action, progress.candidate)
            presentation = get_presentation_snapshot(user_id)
            if presentation is not None and presentation.mode == "desktop":
                await fulfill_desktop_play_intents(
                    db,
                    action,
                    presentation_revision=presentation.revision,
                    allow_autonomous=get_user_proactive_record(user_id).available
                    and await get_disturbance_tier(user_id, db=db) == "autonomous",
                )
        else:
            await enqueue_asset_cleanup(db, user_id, {progress.candidate.video_path, progress.candidate.poster_path})
            progress.candidate = None
            action.generation_state_json = progress.model_dump_json()
            action.status, action.stage, action.error = "failed", "review", "成品未采纳，可手动重做"
            await touch_desktop_state(db, user_id)
        await db.commit()
        return desktop_action_response(action)


async def set_desktop_video_preferences(
    db: AsyncSession,
    user_id: int,
    request: DesktopVideoPreferences,
) -> DesktopVideoStateResponse:
    await desktop_preferences(db, user_id, request)
    return await get_desktop_video_state(db, user_id)


async def play_desktop_action(
    db: AsyncSession,
    user_id: int,
    request: DesktopVideoPlayRequest,
    *,
    source: str = "user_requested",
) -> DesktopVideoPlayCommand:
    if source == "user_requested":
        await _wait_desktop_mode(user_id)
    else:
        _require_desktop_mode(user_id)
    action = await desktop_action(db, user_id, request.action_id)
    if request.expected_set_id is not None and request.expected_set_id != action.set_id:
        raise DesktopVideoStateError("桌面动作组合已经变化")
    state = await require_desktop_current(db, user_id, action.set_id)
    desired, _ = await _desired_context(db, user_id)
    row = await db.get(DesktopVideoSet, action.set_id)
    if row is None or row.context_hash != desired:
        raise DesktopVideoStateError("当前画面正在更新，请等待当前组合准备好")
    if source in {"autonomous", "chat_expression"} and (state.pinned or not state.autonomous_enabled):
        raise DesktopVideoStateError("桌面动作已固定或自主切换已关闭")
    if source == "autonomous":
        if not get_user_proactive_record(user_id).available:
            raise DesktopVideoStateError("当前不能自主切换桌面动作")
        if await get_disturbance_tier(user_id, db=db) != "autonomous":
            raise DesktopVideoStateError("当前打扰档位不允许自主表演")
    _require_desktop_mode(user_id)
    presentation = get_presentation_snapshot(user_id)
    if desktop_accepted_asset(action) is None:
        progress = desktop_progress(action)
        if action.status not in {"queued", "processing", "review_pending"} and not (
            action.status == "failed" and progress is not None and progress.failure_reason == "temporary"
        ):
            raise DesktopVideoStateError("这个桌面动作尚未就绪")
        playback = DesktopVideoPlayback(
            user_id=user_id,
            set_id=action.set_id,
            action_id=action.id,
            play_id=str(uuid4()),
            set_epoch=state.set_epoch,
            presentation_revision=presentation.revision,
            status="waiting",
            source=source,
            expires_at=utc_now() + timedelta(seconds=DEFERRED_PLAY_INTENT_TTL_SECONDS),
        )
        db.add(playback)
        return DesktopVideoPlayCommand(
            play_id=playback.play_id,
            set_id=action.set_id,
            action_id=action.id,
            set_epoch=state.set_epoch,
            kind=action.kind,
            expires_at=playback.expires_at,
            video_url="",
            poster_url="",
            version=action.version,
        )
    return await emit_desktop_play(db, user_id, action, source=source, presentation_revision=presentation.revision)


async def claim_desktop_playback(db: AsyncSession, user_id: int, play_id: str, client_id: str) -> bool:
    _require_desktop_mode(user_id)
    presentation = get_presentation_snapshot(user_id)
    return await claim_desktop_play(db, user_id, play_id, client_id, presentation_revision=presentation.revision)


async def record_desktop_playback(db: AsyncSession, user_id: int, play_id: str, receipt: DesktopVideoReceipt) -> None:
    presentation = get_presentation_snapshot(user_id)
    await record_desktop_receipt(
        db,
        user_id,
        play_id,
        receipt,
        presentation_revision=presentation.revision
        if presentation is not None and presentation.mode == "desktop"
        else -1,
    )


class DesktopProposalVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(approve|reuse|defer|reject)$")
    reason: str = Field(max_length=400)
    reuse_action_id: int | None = None


async def design_desktop_action(
    user_id: int,
    request: DesktopVideoDesignRequest,
    *,
    source: str = "user_requested",
    require_desktop: bool = False,
) -> DesktopVideoProposalResponse:
    retry_action_id: int | None = None
    async with _lock(user_id), SESSION_LOCAL() as db:
        if require_desktop:
            _require_desktop_mode(user_id)
        state = await desktop_state(db, user_id, lock=True)
        if state is None or state.current_set_id is None:
            raise DesktopVideoStateError("请先准备当前桌面生活组合")
        if request.expected_set_id is not None and request.expected_set_id != state.current_set_id:
            raise DesktopVideoStateError("桌面生活组合已经变化")
        if source == "autonomous" and (not state.autonomous_enabled or not SETTINGS.action_autocreate_enabled):
            raise DesktopVideoStateError("自主制作桌面动作当前已关闭")
        current = await load_desktop_visual_snapshot(db, user_id)
        row = await db.get(DesktopVideoSet, state.current_set_id)
        if row is None or row.context_hash != desktop_context_hash(current):
            raise DesktopVideoStateError("请先准备当前穿着和场景的桌面组合")
        normalized = normalize_action_name(request.name)
        key = "custom_" + hashlib.sha256(normalized.encode()).hexdigest()[:24]
        existing_action = await db.scalar(
            select(DesktopVideoAction).where(DesktopVideoAction.set_id == row.id, DesktopVideoAction.key == key),
        )
        if existing_action is not None:
            proposal = await db.scalar(
                select(DesktopVideoProposal)
                .where(
                    DesktopVideoProposal.user_id == user_id,
                    DesktopVideoProposal.set_id == row.id,
                    DesktopVideoProposal.action_id == existing_action.id,
                )
                .order_by(DesktopVideoProposal.id.desc())
                .limit(1),
            )
            response = DesktopVideoProposalResponse(
                proposal_id=proposal.id if proposal else 0,
                set_id=row.id,
                action_id=existing_action.id,
                status=existing_action.status,
                review_reason="同名动作沿用原设计，修改内容请使用新的动作名称",
            )
            if desktop_accepted_asset(existing_action) is not None:
                response.status = "reused"
                return response
            if existing_action.status not in {"queued", "failed"} and existing_action.stage != "paused":
                return response
            durations = {existing_action.duration_seconds}
            if proposal is not None:
                original = DesktopVideoDesignRequest.model_validate_json(proposal.design_json)
                durations.add(original.duration_seconds)
            if (
                request.motion_description != existing_action.description
                or request.kind != existing_action.kind
                or request.duration_seconds not in durations
                or request.use_when != json.loads(existing_action.use_when_json)
                or request.avoid_when != json.loads(existing_action.avoid_when_json)
            ):
                return response
            retry_action_id = existing_action.id
        else:
            fingerprint = hashlib.sha256(
                json.dumps(
                    {"name": normalized, "motion": request.motion_description, "kind": request.kind},
                    sort_keys=True,
                ).encode(),
            ).hexdigest()
            proposal = await db.scalar(
                select(DesktopVideoProposal).where(
                    DesktopVideoProposal.set_id == row.id,
                    DesktopVideoProposal.fingerprint == fingerprint,
                ),
            )
            if proposal is None:
                proposal = DesktopVideoProposal(
                    user_id=user_id,
                    set_id=row.id,
                    fingerprint=fingerprint,
                    design_json=request.model_dump_json(),
                    source=source,
                    status="pending",
                    review_reason="",
                )
                db.add(proposal)
                await db.flush()
                await touch_desktop_state(db, user_id)
            elif proposal.status in {"rejected", "deferred"}:
                if proposal.status == "rejected":
                    return DesktopVideoProposalResponse(
                        proposal_id=proposal.id,
                        set_id=row.id,
                        action_id=proposal.action_id,
                        status=proposal.status,
                        review_reason=proposal.review_reason,
                    )
                proposal.status, proposal.source = "pending", source
            await db.commit()
            response = DesktopVideoProposalResponse(
                proposal_id=proposal.id,
                set_id=row.id,
                action_id=proposal.action_id,
                status=proposal.status,
                review_reason=proposal.review_reason,
            )
    if retry_action_id is not None:
        try:
            action = await generate_desktop_action(user_id, retry_action_id, source=source)
        except (DesktopVideoError, ActionPolicyError, LlmCallBlockedError, VisualReasoningError) as exc:
            response.status, response.review_reason = "deferred", str(exc)
            return response
        response.status = action.status
        if response.proposal_id:
            async with _lock(user_id), SESSION_LOCAL() as db:
                proposal = await db.scalar(
                    select(DesktopVideoProposal).where(
                        DesktopVideoProposal.id == response.proposal_id,
                        DesktopVideoProposal.user_id == user_id,
                        DesktopVideoProposal.action_id == retry_action_id,
                    ),
                )
                if proposal is not None and proposal.status == "deferred":
                    proposal.status = "approved"
                    await touch_desktop_state(db, user_id)
                    await db.commit()
        return response
    _schedule_desktop_review(user_id, response.proposal_id)
    return response


async def _review_desktop_proposal(user_id: int, proposal_id: int, *, allow_generation: bool = True) -> None:
    try:
        async with SESSION_LOCAL() as db:
            proposal = await db.scalar(
                select(DesktopVideoProposal).where(
                    DesktopVideoProposal.id == proposal_id,
                    DesktopVideoProposal.user_id == user_id,
                ),
            )
            if proposal is None or proposal.status != "pending":
                return
            row = await db.get(DesktopVideoSet, proposal.set_id)
            snapshot = DesktopVisualSnapshot.model_validate_json(row.context_json)
            request = DesktopVideoDesignRequest.model_validate_json(proposal.design_json)
            language = resolve_language(await get_user_setting(db, user_id, "language"))
            existing = list(
                await db.scalars(
                    select(DesktopVideoAction).where(
                        DesktopVideoAction.set_id == row.id,
                        DesktopVideoAction.enabled.is_(True),
                        DesktopVideoAction.accepted_asset_json.is_not(None),
                    ),
                ),
            )
            existing_payload = [
                {
                    "id": action.id,
                    "name": action.name,
                    "description": action.description,
                    "kind": action.kind,
                    "duration_seconds": action.duration_seconds,
                    "use_when": json.loads(action.use_when_json),
                    "avoid_when": json.loads(action.avoid_when_json),
                }
                for action in existing
            ]

        async def before_submit() -> None:
            require_new_generation_call(user_id)
            async with SESSION_LOCAL() as db:
                state = await require_desktop_current(db, user_id, proposal.set_id)
                current = await load_desktop_visual_snapshot(db, user_id)
                if desktop_context_hash(current) != desktop_context_hash(snapshot):
                    raise LlmCallBlockedError("穿着或场景已变化")
                if proposal.source == "autonomous" and (
                    not state.autonomous_enabled or not SETTINGS.action_autocreate_enabled
                ):
                    raise LlmCallBlockedError("自主制作当前已关闭")

        identity, outfit = await asyncio.gather(
            asyncio.to_thread(read_asset_data_uri, snapshot.identity_path),
            asyncio.to_thread(read_asset_data_uri, snapshot.outfit_path),
        )
        if not identity or not outfit:
            raise DesktopVideoStateError("参考图片无法读取")
        raw = await vision_chat(
            user_id,
            DESKTOP_ACTION_REVIEW,
            json.dumps(
                {
                    "design": request.model_dump(),
                    "output_language": language,
                    "persona": snapshot.persona,
                    "outfit": snapshot.outfit_description,
                    "environment": snapshot.scene_description,
                    "existing_actions": existing_payload,
                },
                ensure_ascii=False,
            ),
            reference_images=(identity, outfit) if snapshot.outfit_hash != snapshot.identity_hash else (identity,),
            before_submit=before_submit,
        )
        verdict = DesktopProposalVerdict.model_validate(parse_llm_json(raw))
        if verdict.decision == "reuse" and verdict.reuse_action_id not in {action["id"] for action in existing_payload}:
            raise DesktopVideoStateError("评审复用的动作不属于当前可用列表")
        if verdict.decision != "reuse" and verdict.reuse_action_id is not None:
            raise DesktopVideoStateError("评审结果包含不适用的复用动作")
        action_id: int | None = None
        async with _lock(user_id), SESSION_LOCAL() as db:
            current_proposal = await db.get(DesktopVideoProposal, proposal_id)
            if current_proposal is None or current_proposal.status != "pending":
                return
            await require_desktop_current(db, user_id, current_proposal.set_id)
            current_proposal.review_reason = verdict.reason
            if verdict.decision == "approve":
                key = "custom_" + hashlib.sha256(normalize_action_name(request.name).encode()).hexdigest()[:24]
                action = await db.scalar(
                    select(DesktopVideoAction).where(
                        DesktopVideoAction.set_id == current_proposal.set_id,
                        DesktopVideoAction.key == key,
                    ),
                )
                if action is None:
                    action = DesktopVideoAction(
                        user_id=user_id,
                        set_id=current_proposal.set_id,
                        key=key,
                        name=request.name,
                        description=request.motion_description,
                        kind=request.kind,
                        duration_seconds=request.duration_seconds,
                        preset=False,
                        use_when_json=json.dumps(request.use_when, ensure_ascii=False),
                        avoid_when_json=json.dumps(request.avoid_when, ensure_ascii=False),
                        enabled=True,
                        status="queued",
                        stage="prepare",
                        attempt=0,
                        version=0,
                    )
                    db.add(action)
                    await db.flush()
                current_proposal.action_id = action_id = action.id
                current_proposal.status = "approved"
            elif verdict.decision == "reuse":
                current_proposal.action_id = verdict.reuse_action_id
                current_proposal.status = "reused"
            else:
                current_proposal.status = "deferred" if verdict.decision == "defer" else "rejected"
            await touch_desktop_state(db, user_id)
            await db.commit()
        if action_id is not None and allow_generation:
            await generate_desktop_action(user_id, action_id, source=proposal.source)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Desktop action proposal deferred", extra={"proposal_id": proposal_id}, exc_info=True)
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(DesktopVideoProposal).where(
                    DesktopVideoProposal.id == proposal_id,
                    DesktopVideoProposal.user_id == user_id,
                ),
            )
            if row is not None:
                row.status = "deferred"
                row.review_reason = str(exc) if isinstance(exc, DesktopVideoError) else "评审或制作暂未完成，可稍后重试"
                await touch_desktop_state(db, user_id)
                await db.commit()


def _schedule_desktop_review(user_id: int, proposal_id: int, *, allow_generation: bool = True) -> None:
    if not proposal_id or proposal_id in _REVIEWS:
        return
    task = asyncio.create_task(
        _review_desktop_proposal(user_id, proposal_id, allow_generation=allow_generation),
        name=f"desktop-review-{proposal_id}",
    )
    _REVIEWS[proposal_id] = task
    track_user_task(user_id, task)
    task.add_done_callback(lambda _task: _REVIEWS.pop(proposal_id, None))


async def build_desktop_action_context(db: AsyncSession, user_id: int, *, language: str) -> str:
    state = await get_desktop_video_state(db, user_id)
    actions = state.current.actions if state.current else []
    payload = {
        "pinned": state.pinned,
        "autonomous_enabled": state.autonomous_enabled,
        "current_action_id": state.selected_action_id,
        "preparation_error": state.preparation_error,
        "ready_actions": [
            action.model_dump(exclude={"poster_url", "video_url", "candidate_video_url", "candidate_poster_url"})
            for action in actions
            if action.enabled and action.video_url
        ],
        "preparing": [
            {
                "action_id": action.id,
                "name": action.name,
                "status": action.status,
                "stage": action.stage,
                "error": action.error,
            }
            for action in actions
            if action.status != "ready"
        ],
    }
    proposals = list(
        await db.scalars(
            select(DesktopVideoProposal)
            .where(
                DesktopVideoProposal.user_id == user_id,
                DesktopVideoProposal.set_id == (state.current.id if state.current else 0),
            )
            .order_by(DesktopVideoProposal.updated_at.desc())
            .limit(5),
        ),
    )
    payload["proposals"] = [
        {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "action_id": proposal.action_id,
            "reason": proposal.review_reason,
        }
        for proposal in proposals
    ]
    return resolve_prompt_text(DESKTOP_CONTEXT_GUIDANCE, language) + "\n" + json.dumps(payload, ensure_ascii=False)


class DesktopIdleChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: int | None = None
    reason: str = Field(default="", max_length=400)


async def choose_idle_desktop_action(user_id: int, idle_seconds: float, local_hour: int) -> dict[str, object]:
    presentation = get_presentation_snapshot(user_id)
    if presentation is None or presentation.mode != "desktop" or not get_user_proactive_record(user_id).available:
        return {"expressed": False, "action_id": None, "reason": "unavailable"}
    async with SESSION_LOCAL() as db:
        state = await get_desktop_video_state(db, user_id)
        if (
            state.current is None
            or state.pinned
            or not state.autonomous_enabled
            or await get_disturbance_tier(user_id, db=db) != "autonomous"
        ):
            return {"expressed": False, "action_id": None, "reason": "disabled"}
        recent_context = await load_recent_context_window(db, user_id)
        current_set = await db.get(DesktopVideoSet, state.current.id)
        frozen_visual: dict[str, object] = {}
        if current_set is not None:
            try:
                frozen_visual = json.loads(current_set.context_json)
            except (TypeError, ValueError):
                frozen_visual = {}
        actions = [
            {
                "id": action.id,
                "name": action.name,
                "description": action.description,
                "kind": action.kind,
                "use_when": action.use_when,
                "avoid_when": action.avoid_when,
            }
            for action in state.current.actions
            if action.enabled and action.video_url
        ]
    if not actions:
        return {"expressed": False, "action_id": None, "reason": "not_ready"}
    context = await load_companion_prompt_context(user_id)
    if context is None:
        return {"expressed": False, "action_id": None, "reason": "not_ready"}
    raw = await chat(
        user_id,
        DESKTOP_IDLE_EXPRESSION,
        json.dumps(
            {
                "persona": context.persona_extras,
                "output_language": context.language,
                "current_mood": context.current_mood,
                "current_time": context.current_time,
                "long_term_memories": context.memories_block,
                "recent_context": recent_context,
                "environment": {
                    "scene": frozen_visual.get("scene_description", ""),
                    "outfit": frozen_visual.get("outfit_description", ""),
                },
                "idle_seconds": idle_seconds,
                "local_hour": local_hour,
                "current_action_id": state.selected_action_id,
                "actions": actions,
            },
            ensure_ascii=False,
        ),
    )
    choice = DesktopIdleChoice.model_validate(parse_llm_json(raw))
    if (
        choice.action_id is None
        or choice.action_id == state.selected_action_id
        or choice.action_id not in {action["id"] for action in actions}
        or presentation != get_presentation_snapshot(user_id)
    ):
        return {"expressed": False, "action_id": None, "reason": choice.reason or "keep_current"}
    async with SESSION_LOCAL() as db:
        command = await play_desktop_action(
            db,
            user_id,
            DesktopVideoPlayRequest(action_id=choice.action_id, expected_set_id=state.current.id, reason=choice.reason),
            source="autonomous",
        )
        if presentation != get_presentation_snapshot(user_id):
            return {"expressed": False, "action_id": None, "reason": "presentation_changed"}
        await db.commit()
    return {"expressed": True, "action_id": command.action_id, "reason": choice.reason}


async def drain_desktop_reviews() -> None:
    tasks = list(_REVIEWS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def resume_desktop_reviews(user_id: int | None = None, *, allow_generation: bool = True) -> None:
    async with SESSION_LOCAL() as db:
        query = select(DesktopVideoProposal).where(DesktopVideoProposal.status == "pending")
        if user_id is not None:
            query = query.where(DesktopVideoProposal.user_id == user_id)
        rows = list(await db.scalars(query))
    for row in rows:
        _schedule_desktop_review(row.user_id, row.id, allow_generation=allow_generation)
