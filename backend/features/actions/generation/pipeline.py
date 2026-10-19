"""动作制作：pipeline。"""

import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.providers.base import ServiceType, VideoGenRequest
from infrastructure.ai.providers.http import ProviderResultUnknownError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import (
    action_pose_asset_path,
    action_source_asset_path,
    save_action_pose_asset_async,
    save_action_source_asset_async,
    sniff_media_ext,
    unlink_companion_asset,
)
from infrastructure.assets.media_download import download_media_result
from infrastructure.async_io import run_blocking
from infrastructure.video_processing.frames import prepare_action_frame
from infrastructure.video_processing.image import prepare_action_image
from infrastructure.video_processing.matting import matte_video
from infrastructure.video_processing.process import (
    MAX_SOURCE_BYTES,
    TARGET_EXT,
    build_hitmask,
    extract_cover,
    prepare_action_clip,
)
from infrastructure.video_processing.quality import validate_action_clip
from runtime.paid_work import GenerationWorkPaused

from features.actions.generation.script import (
    ActionScriptEntry,
    ImageActionScriptEntry,
    VideoActionScriptEntry,
    build_action_image_prompt,
    build_pose_prompt,
    build_video_prompt,
    inspect_peek_geometry,
)
from features.actions.generation.state import GenerationContext
from features.actions.materials import accepted_action_asset
from features.actions.media_contracts import ActionResult, ImageClipSpec, VideoClipSpec
from features.actions.models import REQUIRED_SYSTEM_SLOTS, CompanionAction, CompanionActionPack
from features.appearance.avatar_service import FULLBODY_SIZE
from features.appearance.character_card import render_character_profile
from features.media.chain import (
    MediaCandidate,
    MediaChainState,
    MediaProviderFailedError,
    media_failure_reason,
    resolve_frozen_media_provider,
    video_failure_message,
    video_provider_failure_reason,
)
from features.media.character_images import ImageChainState, generate_character_images
from features.media.identity_review import review_character_frames, score_character_frames
from features.media.review_contracts import MediaReviewPublication
from features.media.review_service import create_media_review
from features.media.video_queue.contracts import VideoQueueInfo, VideoQueueOwner
from features.media.video_queue.service import (
    acknowledge_video_attempt,
    enqueue_video_attempt,
    wait_video_attempt,
)

from .errors import VideoPackError, VideoPackStateError
from .execution_state import _SOURCE_MEDIA_EXTS, logger
from .helpers import (
    _action_sample_uris,
    _action_video_frame_uri,
    _artifact_abs_path,
    _clip_frame_uris,
    _clip_row_values,
    _hitmask_content_rect,
    _image_data_uri,
    _pack_dir,
)
from .planning import _action_spec, _prepare_action_video_plan
from .references import _require_new_paid_step, require_action_matting_model
from .transactions import _advance_job, _emit_pack_event, _save_action_materials, _save_action_state


async def _prepare_clip(
    work: Path,
    action: str,
    src: Path,
    user_id: int,
    canvas_w: int,
    canvas_h: int,
    *,
    start: float | None = None,
    end: float | None = None,
    preserve_resolution: bool = False,
    directory: str,
) -> ActionResult:
    """处理单动作片段并转存片段、封面与命中遮罩；封面/遮罩从最终交付片段读取（libvpx 解码 VP9 alpha），遮罩落盘供 hitmask_ref 按帧查询。"""
    dst = work / f"{action}.{TARGET_EXT}"
    processed = await run_blocking(
        prepare_action_clip,
        src,
        dst,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        start_seconds=start,
        end_seconds=end,
        preserve_resolution=preserve_resolution,
    )
    canvas_w, canvas_h = processed.width, processed.height
    hitmask = await run_blocking(build_hitmask, dst, canvas_w=canvas_w, canvas_h=canvas_h)
    cover = await run_blocking(extract_cover, dst, canvas_w=canvas_w, canvas_h=canvas_h)
    stored, cover_stored, hitmask_stored = await _save_action_materials(
        user_id,
        directory,
        label_prefix=f"video_{action}",
        media_ext="webm",
        media=await run_blocking(dst.read_bytes),
        cover=cover,
        hitmask=hitmask,
    )
    return ActionResult(
        clip=VideoClipSpec(
            action=action,
            path=stored,
            sha256=processed.sha256,
            frames=processed.frames,
            duration_ms=processed.duration_ms,
            width=processed.width,
            height=processed.height,
            content_rect=_hitmask_content_rect(hitmask),
        ),
        cover_path=cover_stored,
        hitmask_path=hitmask_stored,
    )


async def _prepare_image(
    work: Path,
    action: str,
    src: Path,
    user_id: int,
    canvas_w: int,
    canvas_h: int,
    *,
    directory: str,
) -> ActionResult:
    dst = work / f"{action}.png"
    processed = await run_blocking(prepare_action_image, src, dst, canvas_w=canvas_w, canvas_h=canvas_h)
    stored, cover_stored, hitmask_stored = await _save_action_materials(
        user_id,
        directory,
        label_prefix=f"action_image_{action}",
        media_ext="png",
        media=await run_blocking(dst.read_bytes),
        cover=processed.cover,
        hitmask=processed.hitmask,
    )
    return ActionResult(
        clip=ImageClipSpec(
            action=action,
            path=stored,
            sha256=processed.sha256,
            width=processed.width,
            height=processed.height,
            content_rect=processed.content_rect,
        ),
        cover_path=cover_stored,
        hitmask_path=hitmask_stored,
    )


async def _review_pack_identity(
    pack_id: int,
    user_id: int,
    pack: CompanionActionPack,
    context: GenerationContext,
    results: dict[str, ActionResult],
    *,
    before_submit: Callable[[], Awaitable[None]],
) -> tuple[str, str]:
    """必需动作的静态图或视频采样帧与头像、当前着装合成的冻结参考复核；不可用时转人工预览。"""
    try:
        reference_uri = await run_blocking(_image_data_uri, _artifact_abs_path(pack.reference_path))
        frame_uris: list[str] = []
        for slot in REQUIRED_SYSTEM_SLOTS:
            if slot in results:
                frame_uris.extend(await _action_sample_uris(results[slot].clip))
        return await review_character_frames(
            user_id,
            reference_uri,
            tuple(frame_uris),
            task=AiTaskKey.window_action,
            pack_wide=True,
            identity_text=render_character_profile(context.identity),
            before_submit=before_submit,
        )
    except LlmCallBlockedError:
        raise
    except Exception:
        logger.warning("video pack identity review failed", extra={"pack_id": pack_id}, exc_info=True)
        return "review", "动作包的自动检查未完成，请预览后手动启用"


async def _prepare_action_pose(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: ActionScriptEntry,
    context: GenerationContext,
) -> None:
    if job.pose_path and _artifact_abs_path(job.pose_path).is_file():
        return
    require_action_matting_model()
    reference_uri = await run_blocking(_image_data_uri, _artifact_abs_path(pack.reference_path))
    identity_uri = await run_blocking(_image_data_uri, _artifact_abs_path(pack.reference_path))
    pose_state = (
        ImageChainState.model_validate_json(job.pose_generation_state_json)
        if job.pose_generation_state_json
        else ImageChainState()
    )

    async def save_pose(progress: ImageChainState) -> None:
        job.pose_generation_state_json = progress.model_dump_json(exclude_none=True)
        job.stage = "pose"
        await _advance_job(job.id, stage="pose", pose_generation_state_json=job.pose_generation_state_json)

    prompt = (
        build_action_image_prompt(entry, context.identity)
        if isinstance(entry, ImageActionScriptEntry)
        else build_pose_prompt(
            entry,
            context.identity,
            activity_target=_action_spec(job).semantics_or() if job.definition_id is not None else "",
        )
    )
    paths = await generate_character_images(
        prompt,
        user_id=pack.user_id,
        storage_directory=_pack_dir(pack),
        reference_image=reference_uri,
        identity_reference=identity_uri,
        identity_text=render_character_profile(context.identity),
        size=FULLBODY_SIZE,
        image_edit=True,
        prefer_transparent_background=True,
        prepare_action_frame=job.definition_id is not None,
        goal_review="required" if job.definition_id is not None else "none",
        state=pose_state,
        save_progress=save_pose,
        before_submit=lambda: _require_new_paid_step(pack, context),
        task=AiTaskKey.window_action,
        selection=context.action_image_selection.get(job.key),
    )
    job.pose_path = action_pose_asset_path(
        pack.user_id,
        pose_state.generation_id,
        directory=_pack_dir(pack),
    )
    await _advance_job(job.id, stage="pose", pose_path=job.pose_path)
    pose_data = await run_blocking(_artifact_abs_path(paths[0]).read_bytes)
    prepared = await run_blocking(prepare_action_frame, pose_data, preserve_resolution=entry.media_type == "image")
    await save_action_pose_asset_async(
        prepared,
        user_id=pack.user_id,
        generation_id=pose_state.generation_id,
        directory=_pack_dir(pack),
    )


async def _run_image_pipeline(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: ImageActionScriptEntry,
    context: GenerationContext,
) -> bool:
    if job.result_json:
        result = ActionResult.model_validate_json(job.result_json)
        if result.clip.media_type != "image" or any(
            not _artifact_abs_path(path).is_file()
            for path in (result.clip.path, result.cover_path, result.hitmask_path)
        ):
            raise VideoPackError("已保存的动作图片不可读，请恢复原素材")
    else:
        await _prepare_action_pose(pack, job, entry, context)
        await _advance_job(job.id, stage="process")
        await _emit_pack_event(
            pack.user_id,
            "companion.video.progress",
            {"packId": pack.id, "outfitId": pack.outfit_id, "stage": "process", "action": job.key},
        )
        if not job.pose_path:
            raise VideoPackError("动作图片不可读")
        with tempfile.TemporaryDirectory(prefix="action-image-") as tmp:
            canvas = json.loads(pack.canvas_spec)
            result = await _prepare_image(
                Path(tmp),
                job.key,
                _artifact_abs_path(job.pose_path),
                pack.user_id,
                canvas["width"],
                canvas["height"],
                directory=_pack_dir(pack),
            )
        values = _clip_row_values(result)
        await _advance_job(job.id, stage="process", **values)
        for key, value in values.items():
            setattr(job, key, value)
        job.stage = "process"
    if job.system_slot in ("peek_left", "peek_right"):
        identity_uri = await run_blocking(_image_data_uri, _artifact_abs_path(pack.reference_path))
        sample_uri = await run_blocking(_image_data_uri, _artifact_abs_path(result.clip.path))
        geometry = await inspect_peek_geometry(
            pack.user_id,
            job.system_slot,
            identity_uri,
            sample_uri,
            task=AiTaskKey.window_action,
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        result = result.model_copy(update={"clip": result.clip.model_copy(update={"peek_geometry": geometry})})
    review_id = None
    replacing_current = accepted_action_asset(job) is not None
    if pack.status == "ready" or replacing_current:
        identity_uri = await run_blocking(_image_data_uri, _artifact_abs_path(pack.reference_path))
        verdict, reason = await review_character_frames(
            pack.user_id,
            identity_uri,
            await _action_sample_uris(result.clip),
            task=AiTaskKey.window_action,
            image=True,
            identity_text=render_character_profile(context.identity),
            before_submit=lambda: _require_new_paid_step(pack, context),
        )
        if verdict == "review" or replacing_current:
            review_id = await create_media_review(
                pack.user_id,
                "image",
                result.clip.path,
                reason if verdict == "review" else "新动作已生成，请预览后确认是否替换当前动作",
                publication=MediaReviewPublication(
                    kind="action",
                    pack_id=pack.id,
                    action_id=job.id,
                    title=job.name,
                    system_slot=job.system_slot,
                ),
            )
    pose_state = (
        ImageChainState.model_validate_json(job.pose_generation_state_json)
        if job.pose_generation_state_json
        else ImageChainState()
    )
    best = pose_state.best()
    provider = pose_state.providers[best.attempt] if best is not None else None
    result = result.model_copy(update={"image_origin": pose_state.selected_origin()})
    await _advance_job(
        job.id,
        stage="publish",
        status="review" if review_id else "succeeded",
        **_clip_row_values(result),
        kind=None,
        loopable=None,
        target_duration_seconds=None,
        generation_state_json=None,
        artifact_path=None,
        provider_task_id=None,
        provider=provider.provider if provider else job.provider,
        model=provider.model if provider else job.model,
    )
    return review_id is None


async def _run_action_pipeline(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: ActionScriptEntry,
    context: GenerationContext,
) -> bool:
    """按媒体类型制作与复核；整包与单动作共用，返回是否直接成功。"""
    if isinstance(entry, ImageActionScriptEntry):
        if job.media_type != "image":
            raise VideoPackStateError("动作图片规格与素材类型不符")
        return await _run_image_pipeline(pack, job, entry, context)
    if job.media_type != "video":
        raise VideoPackStateError("视频规格与素材类型不符")
    await _prepare_action_video_plan(job)
    state = MediaChainState.model_validate_json(job.generation_state_json or "")
    identity_uri = await run_blocking(_image_data_uri, _artifact_abs_path(pack.reference_path))
    if (
        state.phase == "ready"
        and state.active_index is None
        and state.needs_next()
        and (not job.pose_path or not _artifact_abs_path(job.pose_path).is_file())
    ):
        await _prepare_action_pose(pack, job, entry, context)

    if state.phase == "submitting" and not state.queue_request_id:
        state.stop_reason = "result_unknown"
        await _save_action_state(job, state)
    while state.phase != "complete" and not state.stop_reason:
        if state.phase == "ready" and state.active_index is None:
            if not state.needs_next():
                break
            await _require_new_paid_step(pack, context)
            try:
                require_action_matting_model()
            except VideoPackStateError:
                if state.best() is None:
                    raise
                # 抠像模型缺失时不再追加付费提交，按已有最佳候选收尾。
                state.stop_reason = "matting_unavailable"
                await _save_action_state(job, state)
                break
            index = state.next_index
            config = await resolve_frozen_media_provider(pack.user_id, "video_gen", state.providers[index])
            if config is None or not state.providers[index].video_resolution:
                state.next_index += 1
                await _save_action_state(job, state)
                continue
            state.begin(index)
            # 排队记录落盘前尚未发送；恢复时可幂等重入本次尝试。
            state.phase = "ready"
            job.provider, job.model = config.provider_name, config.model
            job.provider_task_id = job.artifact_path = None
            await _save_action_state(
                job,
                state,
                stage="submit",
                provider=job.provider,
                model=job.model,
                provider_task_id=None,
                artifact_path=None,
            )
        try:
            if state.phase != "evaluating":
                source_path, result = await _run_action_attempt(pack, job, entry, context, state)
                state.candidates.append(
                    MediaCandidate(
                        path=result.clip.path,
                        attempt=state.active_index or 0,
                        result_json=result.model_dump_json(),
                        artifacts=[source_path, result.clip.path, result.cover_path, result.hitmask_path],
                    ),
                )
                state.phase = "evaluating"
                await _save_action_state(job, state)
            candidate = next(item for item in state.candidates if item.attempt == state.active_index)
            if not candidate.evaluated:
                result = ActionResult.model_validate_json(candidate.result_json or "")
                try:
                    score = await score_character_frames(
                        pack.user_id,
                        identity_uri,
                        await _clip_frame_uris(result.clip),
                        task=AiTaskKey.window_action,
                        identity_text=render_character_profile(context.identity),
                        before_submit=lambda: _require_new_paid_step(pack, context),
                    )
                except LlmCallBlockedError:
                    raise
                except Exception:
                    logger.warning("action identity score unavailable", extra={"action_id": job.id}, exc_info=True)
                    score = None
                state.accept_score(candidate, score)
            state.phase = "ready"
            state.active_index = None
            await _save_action_state(job, state)
        except GenerationWorkPaused:
            raise
        except Exception as exc:
            reason, can_continue = media_failure_reason(exc)
            # 只有明确未提交成功的 API 错误允许技术回退；轮询、下载、后处理继续原任务。
            if can_continue and (state.phase == "submitting" or isinstance(exc, MediaProviderFailedError)):
                failed_request = state.queue_request_id
                state.phase = "ready"
                state.active_index = None
                state.queue_request_id = None
                state.video_queue = None
                job.provider_task_id = None
                if state.next_index >= len(state.providers):
                    state.stop_reason = reason
                await _save_action_state(job, state, provider_task_id=None)
                if failed_request:
                    await acknowledge_video_attempt(failed_request)
                continue
            if reason == "queue_stopped":
                stopped_request = state.queue_request_id
                state.phase, state.active_index, state.video_queue = "ready", None, None
                state.queue_request_id = None
                job.provider_task_id = None
                state.stop_reason = reason
                await _save_action_state(job, state, provider_task_id=None)
                if stopped_request:
                    await acknowledge_video_attempt(stopped_request)
            if state.best() is None and state.phase != "submitting":
                await _save_action_state(job, state)
                raise
            state.stop_reason = reason
            await _save_action_state(job, state)
            if state.best() is None:
                raise
            break
    best = state.best()
    if best is None:
        if state.stop_reason == "result_unknown":
            raise ProviderResultUnknownError("POST", "video_gen")
        if state.stop_reason:
            raise MediaProviderFailedError(video_failure_message(state.stop_reason), reason=state.stop_reason)
        raise VideoPackError("视频生成服务未返回可用素材")
    unused_source = job.artifact_path
    result = ActionResult.model_validate_json(best.result_json or "")
    pose_state = (
        ImageChainState.model_validate_json(job.pose_generation_state_json)
        if job.pose_generation_state_json
        else ImageChainState()
    )
    result = result.model_copy(
        update={
            "image_origin": pose_state.selected_origin(),
            "video_origin": state.providers[best.attempt].generation_origin(ServiceType.video_gen),
        },
    )
    spec = result.clip
    review_id = None
    frames: tuple[str, ...] = ()
    replacing_current = accepted_action_asset(job) is not None
    if pack.status == "ready" or replacing_current:
        try:
            frames = await _clip_frame_uris(spec)
        except Exception:
            logger.warning("video action frame sampling failed", extra={"action_id": job.id}, exc_info=True)
        try:
            if not frames:
                raise VideoPackError("动作视频画面无法读取")
            verdict, reason = await review_character_frames(
                pack.user_id,
                identity_uri,
                frames,
                task=AiTaskKey.window_action,
                identity_text=render_character_profile(context.identity),
                before_submit=lambda: _require_new_paid_step(pack, context),
            )
        except LlmCallBlockedError:
            raise
        except Exception:
            verdict, reason = "review", "动作视频的自动检查未完成，请预览确认"
        if verdict == "review" or replacing_current:
            review_id = await create_media_review(
                pack.user_id,
                "video",
                spec.path,
                reason if verdict == "review" else "新动作已生成，请预览后确认是否替换当前动作",
                publication=MediaReviewPublication(
                    kind="action",
                    pack_id=pack.id,
                    action_id=job.id,
                    title=job.name,
                    system_slot=job.system_slot,
                ),
            )
    unresolved = (
        state.stop_reason == "result_unknown"
        or state.phase == "submitting"
        or (bool(job.provider_task_id) and not (state.result_url or state.source_path))
    )
    if unresolved:
        # 旧最佳素材可以预览或采纳；当前已付费任务仍需保留，不能借旧候选宣告本次提交已结算。
        state.stop_reason = "result_unknown"
    else:
        state.finish()
    await _save_action_state(
        job,
        state,
        stage="publish",
        status="review" if review_id else "succeeded",
        **_clip_row_values(result),
        artifact_path=job.artifact_path if unresolved else best.artifacts[0],
        provider=job.provider if unresolved else state.providers[best.attempt].provider,
        model=job.model if unresolved else state.providers[best.attempt].model,
        provider_task_id=job.provider_task_id if unresolved else None,
        kind=entry.clip_kind,
        target_duration_seconds=entry.duration_seconds,
        loopable=entry.clip_kind == "loop",
        error="后续提交结果未确认，已保留最佳素材" if state.stop_reason == "result_unknown" else None,
    )
    keep = set(best.artifacts) | {pack.reference_path, context.identity_reference_path, job.pose_path}
    if unresolved:
        keep.update(path for path in (job.artifact_path, state.source_path) if path)
        keep.update(path for candidate in state.candidates for path in (candidate.path, *candidate.artifacts))
    if unused_source and unused_source not in keep:
        await asyncio.to_thread(unlink_companion_asset, unused_source)
    for candidate in state.candidates:
        for path in candidate.artifacts:
            if path not in keep:
                await asyncio.to_thread(unlink_companion_asset, path)
    return review_id is None


async def _run_action_attempt(
    pack: CompanionActionPack,
    job: CompanionAction,
    entry: VideoActionScriptEntry,
    context: GenerationContext,
    state: MediaChainState,
) -> tuple[str, ActionResult]:
    """单家供应商尝试：提交或续轮询 → 下载源视频 → 抠像与接点验收；返回 (源视频路径, 处理结果)。"""
    attempt = state.active_index
    if attempt is None:
        raise VideoPackError("动作制作进度无效，请重新生成")

    async def progress(stage: str) -> None:
        job.stage = stage
        await _advance_job(job.id, stage=stage)
        await _emit_pack_event(
            pack.user_id,
            "companion.video.progress",
            {"packId": pack.id, "outfitId": pack.outfit_id, "stage": stage, "action": job.key},
        )

    if not job.artifact_path and state.source_path and _artifact_abs_path(state.source_path).is_file():
        job.artifact_path = state.source_path
        await _advance_job(job.id, stage="process", artifact_path=job.artifact_path)
    source_path = job.artifact_path
    if not source_path:
        if not state.result_url:
            owner = VideoQueueOwner(
                kind="action",
                user_id=pack.user_id,
                entity_id=job.id,
                generation_id=state.generation_id,
            )
            if not state.queue_request_id:
                resolution = state.providers[attempt].video_resolution
                if not resolution:
                    raise VideoPackStateError("动作制作资料缺少分辨率，请重新生成该动作")
                if not job.pose_path or not _artifact_abs_path(job.pose_path).is_file():
                    raise VideoPackError("动作起始姿态图不可读，请恢复原素材")
                pose_uri = await run_blocking(_action_video_frame_uri, _artifact_abs_path(job.pose_path))

                await _require_new_paid_step(pack, context)
                state.queue_request_id = await enqueue_video_attempt(
                    owner,
                    state.providers[attempt],
                    attempt,
                    VideoGenRequest(
                        prompt=build_video_prompt(
                            entry,
                            context.identity,
                            system_slot=job.system_slot or "",
                            feedback=context.feedback,
                            action_feedback=context.action_feedback.get(job.key, ""),
                        ),
                        duration=int(round(entry.duration_seconds)),
                        resolution=resolution,
                        first_frame_image=pose_uri,
                        last_frame_image=pose_uri if entry.clip_kind == "loop" else None,
                    ),
                )
                state.phase = "submitting"
                await _save_action_state(job, state)

            async def on_submitted(task_id: str) -> None:
                job.provider_task_id = task_id
                state.phase = "processing"
                state.video_queue = None
                await _save_action_state(job, state, stage="generate", provider_task_id=task_id)

            async def on_queue(info: VideoQueueInfo | None) -> None:
                state.video_queue = info
                await _save_action_state(job, state)
                await _emit_pack_event(
                    pack.user_id,
                    "companion.video.progress",
                    {
                        "packId": pack.id,
                        "outfitId": pack.outfit_id,
                        "stage": job.stage,
                        "action": job.key,
                        "video_queue": info.model_dump(mode="json") if info else None,
                    },
                )

            if state.phase != "storing":
                await progress("generate")
                status = await wait_video_attempt(
                    state.queue_request_id,
                    on_submitted=on_submitted,
                    on_queue=on_queue,
                )
                if status.status != "succeeded":
                    reason = video_provider_failure_reason(status.error)
                    raise MediaProviderFailedError(video_failure_message(reason), reason=reason)
                state.result_url = status.download_url
                state.phase = "storing"
                state.video_queue = None
                await _save_action_state(job, state, stage="download")
                await acknowledge_video_attempt(state.queue_request_id)
        await progress("download")
        if not state.result_url:
            raise VideoPackError("供应商未返回视频下载地址")
        data = await download_media_result(state.result_url, max_bytes=MAX_SOURCE_BYTES)
        ext = sniff_media_ext(data)
        if ext not in _SOURCE_MEDIA_EXTS:
            raise VideoPackError("供应商返回了不支持的视频格式")
        # 源视频路径先随进度落库，写盘中断后按确定性路径复用。
        state.source_path = action_source_asset_path(
            pack.user_id,
            state.generation_id,
            attempt,
            ext,
            directory=_pack_dir(pack),
        )
        await _save_action_state(job, state, stage="download")
        source_path = await save_action_source_asset_async(
            data,
            user_id=pack.user_id,
            generation_id=state.generation_id,
            directory=_pack_dir(pack),
            attempt=attempt,
            ext=ext,
        )
        job.artifact_path = source_path
        await _advance_job(job.id, stage="process", artifact_path=source_path)
    await progress("process")
    with tempfile.TemporaryDirectory(prefix="video-action-") as tmp:
        work = Path(tmp)
        matte = work / "matte.mkv"
        await run_blocking(matte_video, _artifact_abs_path(source_path), matte)
        await run_blocking(validate_action_clip, matte)
        canvas = json.loads(pack.canvas_spec)
        result = await _prepare_clip(
            work,
            job.key,
            matte,
            pack.user_id,
            canvas["width"],
            canvas["height"],
            preserve_resolution=True,
            directory=_pack_dir(pack),
        )
    return source_path, result
