"""桌面生活视频制作；冻结输入并凭供应商句柄、源文件和候选恢复。"""

import asyncio
import json
from io import BytesIO

from components import SESSION_LOCAL, get_logger, parse_llm_json, resolve_language, track_user_task
from modules.companion import (
    AvatarAsset,
    CompanionOutfit,
    CompanionScene,
    DesktopVideoAction,
    DesktopVideoAsset,
    DesktopVideoProgress,
    DesktopVideoSet,
    DesktopVisualSnapshot,
    Persona,
    desktop_visual_hash,
)
from modules.settings import get_user_setting
from PIL import Image, ImageOps
from prompts.desktop_videos import (
    DESKTOP_ACTION_DESIGN,
    DESKTOP_OUTFIT_FROM_IDENTITY,
    DESKTOP_OUTFIT_REFERENCE,
    DESKTOP_POSE_IMAGE_TEMPLATE,
    DESKTOP_REVIEW_OUTFIT_FROM_IDENTITY,
    DESKTOP_REVIEW_OUTFIT_REFERENCE,
    DESKTOP_VIDEO_LOOP,
    DESKTOP_VIDEO_MOTION_TEMPLATE,
    DESKTOP_VIDEO_ONCE,
    DESKTOP_VIDEO_REVIEW,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import (
    DesktopVideoStateError,
    desktop_action,
    desktop_progress,
    desktop_state,
    emit_desktop_play,
    fulfill_desktop_play_intents,
    publish_desktop_asset,
    touch_desktop_state,
)
from services.domains.companion import (
    get_disturbance_tier,
    get_presentation_snapshot,
    get_user_proactive_record,
    load_persona_definition,
    render_character_identity,
    require_character_snapshot,
)
from services.infrastructure.assets import (
    build_data_uri,
    compute_file_sha256,
    download_media_result,
    read_asset_data_uri,
    resolve_asset_reference,
    save_companion_asset_async,
    save_video_job_asset_async,
)
from services.infrastructure.llm import (
    LlmCallBlockedError,
    ProviderResultUnknownError,
    VideoGenProvider,
    VideoGenRequest,
    build_provider,
    resolve_provider_chain,
    resolve_vision_chain,
    vision_chat,
)
from services.infrastructure.video_processing import (
    VideoProcessError,
    VideoToolUnavailableError,
    prepare_desktop_video,
    sample_desktop_video_frames,
)

from .character_images import ImageChainState, generate_character_images
from .identity_review import score_character_frames
from .image_generation import resolve_image_gen_chain
from .media_chain import (
    MEDIA_IDENTITY_ACCEPT_SCORE,
    FrozenMediaProvider,
    MediaCandidate,
    MediaChainState,
    media_failure_reason,
    resolve_frozen_media_provider,
    video_failure_message,
    video_provider_failure_reason,
)
from .paid_work import GenerationWorkPaused, require_new_generation_call
from .video_jobs import poll_video_task

logger = get_logger(__name__)
_JOBS: dict[int, asyncio.Task[None]] = {}


class DesktopVideoScript(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pose_prompt: str = Field(min_length=1, max_length=1500)
    motion_prompt: str = Field(min_length=1, max_length=2000)


class DesktopVideoReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str = Field(pattern=r"^(pass|review)$")
    reason: str = Field(max_length=500)


class DesktopCandidateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset: DesktopVideoAsset
    verdict: str
    reason: str


def desktop_progress_can_resume(progress: DesktopVideoProgress) -> bool:
    if progress.submission_unknown or progress.failure_reason == "material_rejected":
        return False
    if (
        progress.provider_task_id
        or progress.download_url
        or progress.source_path
        or progress.candidate
        or progress.pose_path
    ):
        return True
    if progress.image_chain_json:
        image = ImageChainState.model_validate_json(progress.image_chain_json)
        return bool(image.candidates or image.source_path or image.result_url)
    return False


def _asset_hash(path: str, *, required: bool = False) -> str:
    resolved = resolve_asset_reference(path)
    if resolved is None:
        if required:
            raise DesktopVideoStateError("伙伴的参考图片尚未准备好，请先完成形象准备")
        return ""
    return compute_file_sha256(resolved[0])


async def load_desktop_visual_snapshot(db: AsyncSession, user_id: int) -> DesktopVisualSnapshot:
    identity = await require_character_snapshot(db, user_id)
    avatar = await db.get(AvatarAsset, identity.avatar_id)
    persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    if avatar is None or avatar.user_id != user_id or not avatar.seed_fullbody_url:
        raise DesktopVideoStateError("伙伴的形象尚未准备好")
    if persona is None or not persona.is_complete or persona.active_scene_id is None:
        raise DesktopVideoStateError("请先准备并启用一个生活场景")
    scene = await db.scalar(
        select(CompanionScene).where(CompanionScene.id == persona.active_scene_id, CompanionScene.user_id == user_id),
    )
    if scene is None or scene.status != "ready" or not scene.description.strip():
        raise DesktopVideoStateError("当前生活场景尚未准备好")
    outfit = await db.scalar(
        select(CompanionOutfit).where(
            CompanionOutfit.user_id == user_id,
            CompanionOutfit.active.is_(True),
            CompanionOutfit.status == "ready",
        ),
    )
    if outfit is None or not outfit.fullbody_url:
        raise DesktopVideoStateError("当前穿着尚未准备好，请先完成衣柜准备")
    identity_hash, outfit_hash, scene_hash = await asyncio.gather(
        asyncio.to_thread(_asset_hash, avatar.seed_fullbody_url, required=True),
        asyncio.to_thread(_asset_hash, outfit.fullbody_url, required=True),
        asyncio.to_thread(_asset_hash, scene.media_path),
    )
    return DesktopVisualSnapshot(
        identity=identity,
        identity_path=avatar.seed_fullbody_url,
        identity_hash=identity_hash,
        outfit_id=outfit.id,
        outfit_path=outfit.fullbody_url,
        outfit_hash=outfit_hash,
        outfit_description=outfit.description or "",
        scene_id=scene.id,
        scene_description=scene.description,
        scene_hash=scene_hash,
        persona=load_persona_definition(persona),
    )


def desktop_context_hash(snapshot: DesktopVisualSnapshot) -> str:
    return desktop_visual_hash(snapshot)


async def resolve_desktop_video_plan(
    user_id: int,
    *,
    kind: str,
    duration_seconds: int,
) -> tuple[int, list[FrozenMediaProvider]]:
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen")
        if not await resolve_vision_chain(db, user_id):
            raise DesktopVideoStateError("请先配置视觉模型")
        images, error = await resolve_image_gen_chain(db, user_id, has_reference=True, multiple_references=True)
        if not images:
            raise DesktopVideoStateError(error or "请配置支持身份和穿着参考的图片模型")
    configured = [(config, build_provider(config, VideoGenProvider)) for config in chain]
    for index, (_, provider) in enumerate(configured):
        if not provider.supports_first_frame or (kind == "loop" and not provider.supports_loop_frames):
            continue
        seconds = next(
            (
                value
                for value in sorted(provider.durations or (duration_seconds,))
                if duration_seconds <= value <= 15
                and provider.max_resolution(duration=value, first_frame=True, last_frame=kind == "loop")
            ),
            None,
        )
        if seconds is None:
            continue
        providers: list[FrozenMediaProvider] = []
        for config, current in configured[index:]:
            if not current.supports_first_frame or (kind == "loop" and not current.supports_loop_frames):
                continue
            if current.durations is not None and seconds not in current.durations:
                continue
            resolution = current.max_resolution(duration=seconds, first_frame=True, last_frame=kind == "loop")
            if resolution:
                frozen = FrozenMediaProvider.from_config(config)
                frozen.video_resolution = resolution
                providers.append(frozen)
        if providers:
            return seconds, providers
    raise DesktopVideoStateError("请配置支持首帧（循环动作还需首尾帧）及所需时长的视频模型")


async def _job_inputs(
    user_id: int,
    action_id: int,
    generation_id: str,
) -> tuple[DesktopVideoAction, DesktopVisualSnapshot, DesktopVideoProgress]:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id)
        progress = desktop_progress(action)
        row = await db.get(DesktopVideoSet, action.set_id)
        if row is None or progress is None or progress.generation_id != generation_id or action.status != "processing":
            raise LlmCallBlockedError("桌面制作任务已经失效")
        snapshot = DesktopVisualSnapshot.model_validate_json(row.context_json)
        db.expunge(action)
        return action, snapshot, progress


async def _require_paid_job(user_id: int, action_id: int, generation_id: str) -> None:
    require_new_generation_call(user_id)
    action, snapshot, _ = await _job_inputs(user_id, action_id, generation_id)
    async with SESSION_LOCAL() as db:
        state = await desktop_state(db, user_id)
        if state is None or state.current_set_id != action.set_id:
            raise DesktopVideoStateError("穿着或场景已经变化，旧任务未继续付费制作")
        current = await load_desktop_visual_snapshot(db, user_id)
    if desktop_context_hash(current) != desktop_context_hash(snapshot):
        raise DesktopVideoStateError("伙伴的资料已经变化，旧任务未继续付费制作")


async def _save_progress(user_id: int, action_id: int, progress: DesktopVideoProgress, *, stage: str) -> None:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id)
        saved = desktop_progress(action)
        if saved is None or saved.generation_id != progress.generation_id or action.status != "processing":
            raise LlmCallBlockedError("桌面制作任务已经失效")
        action.generation_state_json = progress.model_dump_json()
        action.stage = stage
        await touch_desktop_state(db, user_id)
        await db.commit()


async def _references(snapshot: DesktopVisualSnapshot) -> tuple[str, str | None]:
    identity, outfit = await asyncio.gather(
        asyncio.to_thread(read_asset_data_uri, snapshot.identity_path),
        asyncio.to_thread(read_asset_data_uri, snapshot.outfit_path),
    )
    if not identity or not outfit:
        raise DesktopVideoStateError("身份或穿着参考图片无法读取，请先修复对应资产")
    return identity, outfit if snapshot.outfit_hash != snapshot.identity_hash else None


def _fit_pose(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as image, BytesIO() as output:
        ImageOps.exif_transpose(image, in_place=True)
        with image.convert("RGB") as rgb, ImageOps.fit(rgb, (1920, 1080), method=Image.Resampling.LANCZOS) as fitted:
            fitted.save(output, format="PNG")
            return output.getvalue()


async def _prepare_pose(
    user_id: int,
    action: DesktopVideoAction,
    snapshot: DesktopVisualSnapshot,
    progress: DesktopVideoProgress,
    directory: str,
) -> None:
    if progress.pose_path:
        return
    identity, outfit = await _references(snapshot)

    async def before_submit() -> None:
        await _require_paid_job(user_id, action.id, progress.generation_id)

    if not progress.pose_prompt:
        await _save_progress(user_id, action.id, progress, stage="design")
        raw = await vision_chat(
            user_id,
            DESKTOP_ACTION_DESIGN,
            json.dumps(
                {
                    "persona": snapshot.persona,
                    "identity": snapshot.identity.features.model_dump(),
                    "has_outfit_reference": outfit is not None,
                    "outfit": snapshot.outfit_description,
                    "environment": snapshot.scene_description,
                    "action": action.description,
                    "kind": action.kind,
                    "duration_seconds": progress.duration_seconds,
                    "feedback": progress.feedback,
                    "use_when": json.loads(action.use_when_json),
                    "avoid_when": json.loads(action.avoid_when_json),
                },
                ensure_ascii=False,
            ),
            reference_images=(identity, outfit) if outfit else (identity,),
            before_submit=before_submit,
        )
        script = DesktopVideoScript.model_validate(parse_llm_json(raw))
        progress.pose_prompt, progress.motion_prompt = script.pose_prompt, script.motion_prompt
        await _save_progress(user_id, action.id, progress, stage="image")
    image_state = (
        ImageChainState.model_validate_json(progress.image_chain_json)
        if progress.image_chain_json
        else ImageChainState()
    )

    async def save_image(state: ImageChainState) -> None:
        progress.image_chain_json = state.model_dump_json()
        await _save_progress(user_id, action.id, progress, stage="image")

    prompt = DESKTOP_POSE_IMAGE_TEMPLATE.format(
        outfit_reference=DESKTOP_OUTFIT_REFERENCE if outfit else DESKTOP_OUTFIT_FROM_IDENTITY,
        pose=progress.pose_prompt,
        environment=snapshot.scene_description,
        outfit=snapshot.outfit_description,
        identity=render_character_identity(snapshot.identity),
    )
    paths = await generate_character_images(
        prompt,
        user_id=user_id,
        storage_directory=directory,
        reference_image=identity,
        secondary_reference_image=outfit,
        identity_reference=identity,
        identity_text=render_character_identity(snapshot.identity),
        size="1920x1080",
        state=image_state,
        save_progress=save_image,
        before_submit=before_submit,
    )
    resolved = resolve_asset_reference(paths[0])
    if resolved is None:
        raise DesktopVideoStateError("起始画面无法读取")
    fitted = await asyncio.to_thread(_fit_pose, await asyncio.to_thread(resolved[0].read_bytes))
    progress.pose_path = await save_companion_asset_async(
        fitted,
        user_id=user_id,
        label="desktop_pose",
        ext="png",
        directory=directory,
    )
    await _save_progress(user_id, action.id, progress, stage="video")


async def _process_candidate(
    user_id: int,
    action: DesktopVideoAction,
    progress: DesktopVideoProgress,
    directory: str,
) -> DesktopVideoAsset:
    resolved = resolve_asset_reference(progress.source_path)
    if resolved is None:
        raise DesktopVideoStateError("已下载的视频无法读取")
    source = resolved[0]
    temporary = source.with_name(f"desktop-normalized-{progress.generation_id}.mp4")
    processing = asyncio.create_task(asyncio.to_thread(prepare_desktop_video, source, temporary))
    try:
        try:
            result = await asyncio.shield(processing)
        except asyncio.CancelledError:
            await asyncio.gather(processing, return_exceptions=True)
            raise
        video = await save_companion_asset_async(
            await asyncio.to_thread(temporary.read_bytes),
            user_id=user_id,
            label="desktop_video",
            ext="mp4",
            directory=directory,
        )
        poster = await save_companion_asset_async(
            result.cover,
            user_id=user_id,
            label="desktop_cover",
            ext="webp",
            directory=directory,
        )
    finally:
        await asyncio.to_thread(temporary.unlink, missing_ok=True)
    return DesktopVideoAsset(
        video_path=video,
        poster_path=poster,
        width=result.width,
        height=result.height,
        duration_ms=result.duration_ms,
    )


async def _evaluate_candidate(
    user_id: int,
    action: DesktopVideoAction,
    snapshot: DesktopVisualSnapshot,
    progress: DesktopVideoProgress,
) -> None:
    candidate = progress.candidate
    if candidate is None:
        raise DesktopVideoStateError("桌面视频候选缺失")
    try:
        await _require_paid_job(user_id, action.id, progress.generation_id)
    except DesktopVideoStateError:
        progress.review_verdict, progress.review_reason = "review", "资料已经变化，成品保留供预览确认"
        return
    resolved = resolve_asset_reference(candidate.video_path)
    if resolved is None:
        raise DesktopVideoStateError("桌面视频候选无法读取")
    samples = await asyncio.to_thread(sample_desktop_video_frames, resolved[0])
    uris = tuple(build_data_uri(frame, "image/webp") for frame in samples.frames)
    try:
        identity, outfit = await _references(snapshot)
    except (DesktopVideoStateError, OSError):
        progress.review_verdict, progress.review_reason = "review", "参考图片无法读取，成品保留供预览确认"
        await _save_progress(user_id, action.id, progress, stage="review")
        return
    async with SESSION_LOCAL() as db:
        language = resolve_language(await get_user_setting(db, user_id, "language"))

    async def before_submit() -> None:
        await _require_paid_job(user_id, action.id, progress.generation_id)

    if progress.identity_score is None:
        progress.identity_score = await score_character_frames(
            user_id,
            identity,
            uris,
            identity_text=render_character_identity(snapshot.identity),
            before_submit=before_submit,
        )
        await _save_progress(user_id, action.id, progress, stage="review")
        if progress.identity_score is None:
            progress.review_verdict, progress.review_reason = "review", "身份检查未完成，请预览确认"
            return
    if progress.review_verdict is None:
        try:
            raw = await vision_chat(
                user_id,
                DESKTOP_VIDEO_REVIEW.format(
                    outfit_reference=DESKTOP_REVIEW_OUTFIT_REFERENCE if outfit else DESKTOP_REVIEW_OUTFIT_FROM_IDENTITY,
                    frame_start=3 if outfit else 2,
                ),
                json.dumps(
                    {
                        "outfit": snapshot.outfit_description,
                        "environment": snapshot.scene_description,
                        "action": action.description,
                        "kind": action.kind,
                        "frame_times_seconds": samples.times_seconds,
                        "output_language": language,
                    },
                    ensure_ascii=False,
                ),
                reference_images=(identity, outfit, *uris) if outfit else (identity, *uris),
                before_submit=before_submit,
            )
            review = DesktopVideoReview.model_validate(parse_llm_json(raw))
            progress.review_verdict, progress.review_reason = review.verdict, review.reason
        except LlmCallBlockedError:
            raise
        except Exception:
            logger.warning("Desktop video review unavailable", extra={"action_id": action.id}, exc_info=True)
            progress.review_verdict, progress.review_reason = "review", "自动检查未完成，请预览确认"
    await _save_progress(user_id, action.id, progress, stage="review")


async def _finish_job(user_id: int, action_id: int, progress: DesktopVideoProgress) -> None:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id)
        saved = desktop_progress(action)
        if saved is None or saved.generation_id != progress.generation_id or action.status != "processing":
            return
        action.generation_state_json = progress.model_dump_json()
        if progress.candidate is None:
            raise DesktopVideoStateError("桌面视频没有可用成品")
        if (
            progress.review_verdict == "pass"
            and progress.identity_score is not None
            and progress.identity_score >= MEDIA_IDENTITY_ACCEPT_SCORE
        ):
            await publish_desktop_asset(db, action, progress.candidate)
            state = await desktop_state(db, user_id)
            presentation = get_presentation_snapshot(user_id)
            fulfilled = False
            if presentation is not None and presentation.mode == "desktop":
                fulfilled = await fulfill_desktop_play_intents(
                    db,
                    action,
                    presentation_revision=presentation.revision,
                    allow_autonomous=get_user_proactive_record(user_id).available
                    and await get_disturbance_tier(user_id, db=db) == "autonomous",
                )
            if (
                not fulfilled
                and action.key == "idle"
                and state is not None
                and state.current_set_id == action.set_id
                and state.selected_action_id == action.id
                and presentation is not None
                and presentation.mode == "desktop"
            ):
                await emit_desktop_play(
                    db,
                    user_id,
                    action,
                    source="preparation",
                    presentation_revision=presentation.revision,
                )
        else:
            action.status, action.stage = "review_pending", "review"
            action.error = progress.review_reason or "角色外形可能有变化，请预览确认"
            await touch_desktop_state(db, user_id)
        await db.commit()


async def _pause_desktop_job(user_id: int, action_id: int, generation_id: str) -> None:
    async with SESSION_LOCAL() as db:
        action = await desktop_action(db, user_id, action_id)
        progress = desktop_progress(action)
        if progress is None or progress.generation_id != generation_id or action.status != "processing":
            return
        action.stage = "paused"
        action.error = None
        progress.failure_reason = "temporary"
        action.generation_state_json = progress.model_dump_json()
        await touch_desktop_state(db, user_id)
        await db.commit()


async def _run_desktop_job(user_id: int, action_id: int, *, allow_paid_steps: bool = True) -> None:
    generation_id = ""
    try:
        async with SESSION_LOCAL() as db:
            action = await desktop_action(db, user_id, action_id)
            initial = desktop_progress(action)
            if initial is None or action.status != "processing":
                return
            generation_id = initial.generation_id
        action, snapshot, progress = await _job_inputs(user_id, action_id, generation_id)
        directory = f"desktop/{action.set_id}/{action.id}/{generation_id}"
        if not allow_paid_steps and progress.candidate is not None and progress.review_verdict is not None:
            await _finish_job(user_id, action_id, progress)
            return
        if not allow_paid_steps and not (progress.provider_task_id or progress.download_url or progress.source_path):
            raise GenerationWorkPaused
        if not (progress.provider_task_id or progress.download_url or progress.source_path or progress.candidate):
            await _prepare_pose(user_id, action, snapshot, progress, directory)
        chain = MediaChainState.model_validate_json(progress.video_chain_json or "{}")
        if not chain.providers:
            raise DesktopVideoStateError("桌面视频制作规格缺失，请重做")
        if progress.submission_unknown:
            raise DesktopVideoStateError(video_failure_message("result_unknown"))
        while True:
            if progress.candidate is not None:
                if not allow_paid_steps:
                    raise GenerationWorkPaused
                await _evaluate_candidate(user_id, action, snapshot, progress)
                result = DesktopCandidateResult(
                    asset=progress.candidate,
                    verdict=progress.review_verdict or "review",
                    reason=progress.review_reason,
                )
                candidate = MediaCandidate(
                    path=progress.candidate.video_path,
                    attempt=chain.active_index or 0,
                    evaluated=True,
                    accepted=progress.review_verdict == "pass",
                    result_json=result.model_dump_json(),
                )
                chain.accept_score(candidate, progress.identity_score)
                if not any(item.path == candidate.path for item in chain.candidates):
                    chain.candidates.append(candidate)
                if chain.needs_next():
                    progress.candidate = None
                    progress.identity_score = None
                    progress.review_verdict = None
                    progress.review_reason = ""
                    progress.provider_task_id = None
                    progress.download_url = None
                    progress.source_path = None
                    chain.active_index = None
                    chain.phase = "ready"
                    progress.video_chain_json = chain.model_dump_json()
                    await _save_progress(user_id, action_id, progress, stage="video")
                    continue
                best = chain.best()
                if best is not None and best.result_json:
                    chosen = DesktopCandidateResult.model_validate_json(best.result_json)
                    progress.candidate, progress.identity_score = chosen.asset, best.score
                    progress.review_verdict, progress.review_reason = chosen.verdict, chosen.reason
                break
            if progress.source_path:
                await _save_progress(user_id, action_id, progress, stage="process")
                progress.candidate = await _process_candidate(user_id, action, progress, directory)
                await _save_progress(user_id, action_id, progress, stage="review")
                continue
            if progress.download_url:
                data = await download_media_result(progress.download_url, max_bytes=256 * 1024 * 1024, timeout=180)
                progress.source_path = await save_video_job_asset_async(
                    data,
                    user_id=user_id,
                    job_id=action.id,
                    attempt=chain.active_index or chain.next_index,
                    generation_id=generation_id,
                    directory=directory,
                )
                await _save_progress(user_id, action_id, progress, stage="process")
                continue
            if chain.phase == "submitting" and not progress.provider_task_id:
                progress.submission_unknown = True
                await _save_progress(user_id, action_id, progress, stage="video")
                raise DesktopVideoStateError(video_failure_message("result_unknown"))
            index = chain.active_index if progress.provider_task_id else chain.next_index
            if index is None or index >= len(chain.providers):
                raise DesktopVideoStateError(video_failure_message("provider_failed"))
            config = await resolve_frozen_media_provider(user_id, "video_gen", chain.providers[index])
            if config is None:
                raise DesktopVideoStateError(video_failure_message("provider_unavailable"))
            provider = build_provider(config, VideoGenProvider)
            if not progress.provider_task_id:
                if not allow_paid_steps:
                    raise GenerationWorkPaused
                await _require_paid_job(user_id, action_id, generation_id)
                pose = await asyncio.to_thread(read_asset_data_uri, progress.pose_path)
                if not pose:
                    raise DesktopVideoStateError("起始画面无法读取")
                chain.begin(index)
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, action_id, progress, stage="video")
                try:
                    submitted = await provider.submit(
                        VideoGenRequest(
                            prompt=DESKTOP_VIDEO_MOTION_TEMPLATE.format(
                                duration_seconds=progress.duration_seconds,
                                cycle=DESKTOP_VIDEO_LOOP if action.kind == "loop" else DESKTOP_VIDEO_ONCE,
                                motion=progress.motion_prompt,
                            ),
                            duration=progress.duration_seconds,
                            resolution=chain.providers[index].video_resolution,
                            first_frame_image=pose,
                            last_frame_image=pose if action.kind == "loop" else None,
                            aspect_ratio="16:9",
                        ),
                    )
                except ProviderResultUnknownError:
                    progress.submission_unknown = True
                    await _save_progress(user_id, action_id, progress, stage="video")
                    raise DesktopVideoStateError(video_failure_message("result_unknown")) from None
                except LlmCallBlockedError:
                    raise
                except Exception as exc:
                    reason, can_fallback = media_failure_reason(exc)
                    if reason in {"result_unknown", "submit_result_unknown", "timeout"}:
                        progress.submission_unknown = True
                        await _save_progress(user_id, action_id, progress, stage="video")
                        raise DesktopVideoStateError(video_failure_message("result_unknown")) from exc
                    chain.phase = "ready"
                    chain.active_index = None
                    progress.video_chain_json = chain.model_dump_json()
                    await _save_progress(user_id, action_id, progress, stage="video")
                    if not can_fallback or chain.next_index >= len(chain.providers):
                        raise DesktopVideoStateError(video_failure_message("submit_failed")) from None
                    continue
                progress.provider_task_id = submitted.task_id
                chain.phase = "processing"
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, action_id, progress, stage="video")

            async def before_poll() -> None:
                await _job_inputs(user_id, action_id, generation_id)

            status = await poll_video_task(provider, progress.provider_task_id, before_poll=before_poll)
            if status.status == "failed":
                progress.provider_task_id = None
                chain.phase = "ready"
                chain.active_index = None
                progress.video_chain_json = chain.model_dump_json()
                await _save_progress(user_id, action_id, progress, stage="video")
                if chain.next_index >= len(chain.providers):
                    raise DesktopVideoStateError(video_failure_message(video_provider_failure_reason(status.error)))
                continue
            if not status.download_url:
                raise DesktopVideoStateError(video_failure_message("download_failed"))
            progress.download_url = status.download_url
            await _save_progress(user_id, action_id, progress, stage="download")
        chain.finish()
        progress.video_chain_json = chain.model_dump_json()
        await _finish_job(user_id, action_id, progress)
    except GenerationWorkPaused:
        if generation_id:
            await _pause_desktop_job(user_id, action_id, generation_id)
        logger.info("Desktop generation paused for maintenance", extra={"action_id": action_id})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Desktop video job failed", extra={"action_id": action_id}, exc_info=True)
        async with SESSION_LOCAL() as db:
            row = await db.scalar(
                select(DesktopVideoAction).where(
                    DesktopVideoAction.id == action_id,
                    DesktopVideoAction.user_id == user_id,
                ),
            )
            saved = desktop_progress(row) if row else None
            if (
                row is not None
                and saved is not None
                and saved.generation_id == generation_id
                and row.status == "processing"
            ):
                saved.failure_reason = (
                    "result_unknown"
                    if saved.submission_unknown
                    else "material_rejected"
                    if isinstance(exc, VideoProcessError) and not isinstance(exc, VideoToolUnavailableError)
                    else "temporary"
                    if saved.provider_task_id or saved.source_path or saved.candidate
                    else "failed"
                )
                row.generation_state_json = saved.model_dump_json()
                row.status = "failed"
                row.error = (
                    str(exc)
                    if isinstance(exc, (DesktopVideoStateError, VideoProcessError))
                    else "桌面视频制作失败，请查看状态后重试"
                )
                await touch_desktop_state(db, user_id)
                await db.commit()


def schedule_desktop_video_job(user_id: int, action_id: int, *, allow_paid_steps: bool = True) -> None:
    if action_id in _JOBS:
        return
    task = asyncio.create_task(
        _run_desktop_job(user_id, action_id, allow_paid_steps=allow_paid_steps),
        name=f"desktop-video-{action_id}",
    )
    _JOBS[action_id] = task
    track_user_task(user_id, task, cancel_on_maintenance=False)

    def finished(done: asyncio.Task[None]) -> None:
        _JOBS.pop(action_id, None)
        if not done.cancelled() and done.exception() is not None:
            logger.error("Desktop task finalization failed", exc_info=done.exception())

    task.add_done_callback(finished)


async def resume_desktop_video_jobs(user_id: int | None = None, *, allow_paid_steps: bool = False) -> None:
    async with SESSION_LOCAL() as db:
        query = select(DesktopVideoAction).where(DesktopVideoAction.status == "processing")
        if user_id is not None:
            query = query.where(DesktopVideoAction.user_id == user_id)
        rows = list(await db.scalars(query))
    for row in rows:
        progress = desktop_progress(row)
        safe_resume = (
            progress is not None
            and not progress.submission_unknown
            and bool(progress.provider_task_id or progress.download_url or progress.source_path)
        )
        if progress is not None and not progress.submission_unknown and (allow_paid_steps or safe_resume):
            schedule_desktop_video_job(row.user_id, row.id, allow_paid_steps=allow_paid_steps)
        elif not allow_paid_steps and progress is not None and desktop_progress_can_resume(progress):
            if progress.candidate is not None and progress.review_verdict is not None:
                schedule_desktop_video_job(row.user_id, row.id, allow_paid_steps=False)
            else:
                await _pause_desktop_job(row.user_id, row.id, progress.generation_id)
        else:
            async with SESSION_LOCAL() as db:
                current = await desktop_action(db, row.user_id, row.id)
                current.status = "failed"
                if progress is not None:
                    progress.failure_reason = "temporary" if desktop_progress_can_resume(progress) else "failed"
                    current.generation_state_json = progress.model_dump_json()
                current.error = (
                    "制作已中断，请手动继续"
                    if not progress or not progress.submission_unknown
                    else video_failure_message("result_unknown")
                )
                await touch_desktop_state(db, row.user_id)
                await db.commit()


async def resume_user_desktop_video_jobs(user_id: int) -> None:
    await resume_desktop_video_jobs(user_id, allow_paid_steps=True)


async def drain_desktop_video_jobs() -> None:
    tasks = list(_JOBS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
