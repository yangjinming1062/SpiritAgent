"""为已审阅的近况制作并交付画面，沿原槽位恢复已提交的工作。"""

import asyncio
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, TypedDict

from features.accounts.queries import user_is_active
from features.appearance.avatar_service import AvatarSourceUnreadableError
from features.appearance.identity import VisualIdentityNotReadyError, load_visual_identity
from features.appearance.visual_identity import (
    build_plain_image_prompt,
    build_plain_video_prompt,
    build_self_image_inputs,
    build_self_video_inputs,
    load_self_visual_context,
)
from features.configuration.settings.values import load_user_settings
from features.life.episode_media import (
    EPISODE_MEDIA_DIRECTORY,
    EpisodeMediaWork,
    pending_episode_media_work,
    read_episode_media_work,
    settle_episode_media,
    write_episode_media_progress,
)
from features.life.schemas import EpisodeImagesPlan, EpisodeVideoPlan, LifeShareMedia
from features.media.character_images import ImageChainState, generate_reviewed_images, run_frozen_image_chain
from features.media.continuations import MediaWait, wait_for_media
from features.media.contracts import VideoJobSnapshot
from features.media.identity_contracts import CharacterReferenceRole, VisualIdentitySnapshot
from features.media.identity_review import require_image_goal_review_config
from features.media.image_generation import ImageGenerationError, ImageResultDownloadError, resolve_image_gen_chain
from features.media.queries import get_job
from features.media.reference_reader import visual_identity_is_current
from features.media.review_evidence import review_media_intact
from features.media.video_jobs import (
    accept_checked_video,
    prepare_video_job,
    submit_video_job,
    video_output_options,
)
from features.visuals.queries import actual_presentation_snapshot
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import resolve_asset_reference, save_companion_asset_async
from infrastructure.assets.image_references import require_owned_image_path
from infrastructure.assets.write_protection import asset_write_context
from infrastructure.async_io import run_blocking
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import VideoProcessError
from PIL import Image
from pydantic import BaseModel, ConfigDict
from runtime.background import TaskScope
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.tools.toolsets import disabled_backend_tool_names
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
_TASK = AiTaskKey.proactive
_TASKS = TaskScope[int]("life.episode.media")
_VIDEO_ASPECT_RATIO = "16:9"
_VIDEO_RESOLUTIONS = ("768P", "720P", "1080P", "512P", "480P")
VIDEO_CONTINUATION_KIND = "life.episode.video"
_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
_VIDEO_TERMINAL = {"succeeded", "failed", "discarded", "cancelled"}


class EpisodeMediaCapabilities(TypedDict):
    images: dict[str, bool]
    video: dict[str, list[int]]


class _MediaPermissionChanged(ValueError):
    """冻结身份或制作资格已失效。"""


class _FrozenEpisodeMedia(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["image", "video"]
    alt: str
    goal: str
    prompt: str
    available: bool = True
    input_paths: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    identity: VisualIdentitySnapshot | None = None
    reference_role: CharacterReferenceRole = "portrait"
    identity_path: str | None = None
    portrait_path: str | None = None
    reference_guidance: str = ""
    performance_context: str = ""
    pose_goal: str | None = None
    duration: int | None = None
    resolution: str | None = None


async def _image_enabled(db: AsyncSession, user_id: int) -> bool:
    settings = await load_user_settings(db, user_id, ("toolsets.disabled",))
    return "image_generate" not in disabled_backend_tool_names(settings)


async def _video_options(db: AsyncSession, user_id: int, reference_count: int) -> dict[int, str]:
    options = await video_output_options(
        db, user_id, task=_TASK, reference_count=reference_count, preferred_resolutions=_VIDEO_RESOLUTIONS
    )
    return {duration: resolution for duration, resolution in options.items() if 2 <= duration <= 10}


async def episode_media_capabilities(user_id: int) -> EpisodeMediaCapabilities:
    images = {"plain": False, "self": False}
    video: dict[str, list[int]] = {"plain": [], "self": []}
    try:
        await require_image_goal_review_config(user_id, _TASK)
    except (MissingLlmConfigError, ValueError):
        return {"images": images, "video": video}
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id):
            return {"images": images, "video": video}
        self_ready = await load_visual_identity(db, user_id) is not None
        if await _image_enabled(db, user_id):
            for mode, references in (("plain", False), ("self", True)):
                if references and not self_ready:
                    continue
                try:
                    chain = await resolve_image_gen_chain(
                        db, user_id, task=_TASK, has_reference=references, multiple_references=references
                    )
                    images[mode] = bool(chain.providers)
                except (MissingLlmConfigError, ValueError):
                    logger.warning("Episode image configuration unavailable", extra={"user_id": user_id, "mode": mode})
        for mode, reference_count in (("plain", 0), ("self", 2)):
            if reference_count and not self_ready:
                continue
            try:
                video[mode] = list(await _video_options(db, user_id, reference_count))
            except (MissingLlmConfigError, ValueError):
                logger.warning("Episode video configuration unavailable", extra={"user_id": user_id, "mode": mode})
    return {"images": images, "video": video}


async def prepare_life_share_media(user_id: int, media: LifeShareMedia) -> list[dict[str, Any]]:
    """冻结已获准的媒体目标、身份与参考路径。正式文件由近况受理事务接手。"""
    if not isinstance(media, (EpisodeImagesPlan, EpisodeVideoPlan)):
        return []
    try:
        await require_image_goal_review_config(user_id, _TASK)
        plans = await _prepare_media(user_id, media)
    except (
        AvatarSourceUnreadableError,
        VisualIdentityNotReadyError,
        MissingLlmConfigError,
        OSError,
        ValueError,
    ) as exc:
        logger.info("Episode media preparation unavailable", extra={"user_id": user_id, "reason": type(exc).__name__})
        return []
    return [plan.model_dump(mode="json") for plan in plans]


async def _prepare_media(user_id: int, media: EpisodeImagesPlan | EpisodeVideoPlan) -> list[_FrozenEpisodeMedia]:
    depicts_self = (
        any(item.depicts_self for item in media.images) if isinstance(media, EpisodeImagesPlan) else media.depicts_self
    )
    appearance = None
    if depicts_self:
        try:
            appearance = await load_self_visual_context(user_id, await actual_presentation_snapshot(user_id))
        except (AvatarSourceUnreadableError, VisualIdentityNotReadyError):
            if isinstance(media, EpisodeVideoPlan):
                return []
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id):
            return []
        if isinstance(media, EpisodeImagesPlan):
            if not await _image_enabled(db, user_id):
                return []
            plans: list[_FrozenEpisodeMedia] = []
            for item in media.images:
                if item.depicts_self and appearance is None:
                    plans.append(
                        _FrozenEpisodeMedia(
                            kind="image", alt=item.alt, goal=item.prompt, prompt=item.prompt, available=False
                        )
                    )
                    continue
                self_inputs = (
                    build_self_image_inputs(appearance, item.prompt) if item.depicts_self and appearance else None
                )
                prompt = self_inputs.prompt if self_inputs else build_plain_image_prompt(item.prompt)
                references = (
                    tuple(filter(None, (self_inputs.reference_image, self_inputs.secondary_reference_image)))
                    if self_inputs
                    else ()
                )
                chain = await resolve_image_gen_chain(
                    db,
                    user_id,
                    task=_TASK,
                    has_reference=bool(references),
                    multiple_references=len(references) > 1,
                    prompt_chars=len(prompt),
                )
                plans.append(
                    _FrozenEpisodeMedia(
                        kind="image",
                        alt=item.alt,
                        goal=item.prompt,
                        prompt=prompt,
                        available=bool(chain.providers),
                        input_paths=references,
                        references=references,
                        identity=self_inputs.identity if self_inputs else None,
                        reference_role=appearance.context.identity_reference_role
                        if self_inputs and appearance
                        else "portrait",
                        pose_goal=self_inputs.pose_goal if self_inputs else None,
                    )
                )
            return plans if any(plan.available for plan in plans) else []
        inputs = await build_self_video_inputs(appearance) if appearance else None
        reference_count = len(inputs.references) if inputs else 0
        options = await _video_options(db, user_id, reference_count)
        if not options:
            return []
    duration = min(options, key=lambda value: (abs(value - 5), value))
    if appearance and inputs:
        context = appearance.context
        identity_path = await require_owned_image_path(context.fullbody_path, user_id=user_id)
        portrait_path = (
            await require_owned_image_path(context.portrait_path, user_id=user_id) if context.portrait_path else None
        )
        references = inputs.references
        return [
            _FrozenEpisodeMedia(
                kind="video",
                alt=media.alt,
                goal=media.prompt,
                prompt=media.prompt,
                input_paths=tuple(dict.fromkeys(filter(None, (*references, identity_path, portrait_path)))),
                references=references,
                identity=context.identity,
                reference_role=inputs.identity_reference_role,
                identity_path=identity_path,
                portrait_path=portrait_path,
                reference_guidance=inputs.reference_guidance,
                performance_context=context.performance_context,
                duration=duration,
                resolution=options[duration],
            )
        ]
    return [
        _FrozenEpisodeMedia(
            kind="video",
            alt=media.alt,
            goal=media.prompt,
            prompt=build_plain_video_prompt(media.prompt),
            duration=duration,
            resolution=options[duration],
        )
    ]


def _directory(work: EpisodeMediaWork) -> str:
    return f"{EPISODE_MEDIA_DIRECTORY}/{work.id}"


async def _check_identity(db: AsyncSession, work: EpisodeMediaWork, plan: _FrozenEpisodeMedia) -> None:
    if plan.identity is not None and not await visual_identity_is_current(db, work.user_id, plan.identity):
        raise _MediaPermissionChanged("原出镜身份已不可用")


async def _check_image(
    db: AsyncSession, work: EpisodeMediaWork, plan: _FrozenEpisodeMedia, *, share_lock: bool = False
) -> None:
    require_new_generation_call(work.user_id)
    if not await user_is_active(db, work.user_id, share_lock=share_lock):
        raise GenerationWorkPaused
    if not await _image_enabled(db, work.user_id):
        raise _MediaPermissionChanged("图片生成已关闭")
    await _check_identity(db, work, plan)


async def _save_image_progress(work: EpisodeMediaWork, state: ImageChainState) -> None:
    async with SESSION_LOCAL() as db:
        await write_episode_media_progress(
            db, work.user_id, work.id, state="running", checkpoint=state.model_dump(mode="json")
        )
        await db.commit()


async def _finish(work: EpisodeMediaWork, *, state: Literal["failed", "unknown"]) -> None:
    async with SESSION_LOCAL() as db:
        await settle_episode_media(db, work.user_id, work.id, state=state)
        await db.commit()


async def _produce_image(work: EpisodeMediaWork, plan: _FrozenEpisodeMedia) -> None:
    if work.state == "unknown":
        return
    if not plan.available:
        await _finish(work, state="failed")
        return
    chain = ImageChainState.model_validate(work.checkpoint)
    save_progress = partial(_save_image_progress, work)

    async def guard() -> None:
        async with SESSION_LOCAL() as db:
            await _check_image(db, work, plan)

    await guard()
    try:
        if chain.inputs:
            await run_frozen_image_chain(
                work.user_id, chain, task=_TASK, save_progress=save_progress, before_submit=guard
            )
        else:
            reference = plan.references[0] if plan.references else ""
            outfit = plan.references[1] if len(plan.references) > 1 else None
            await generate_reviewed_images(
                plan.prompt,
                user_id=work.user_id,
                task=_TASK,
                storage_directory=_directory(work),
                reference_image=reference,
                identity_reference=reference,
                secondary_reference_image=outfit,
                identity_secondary_reference=outfit,
                identity_required=plan.identity is not None,
                identity_reference_mode="primary" if plan.reference_role == "fullbody" else "portrait_and_outfit",
                goal_review="required",
                delivery_goal=plan.goal,
                pose_goal=plan.pose_goal,
                state=chain,
                save_progress=save_progress,
                before_submit=guard,
            )
    except ImageResultDownloadError:
        chain.resume_download()
        await save_progress(chain)
        return
    except ImageGenerationError as exc:
        await _finish(work, state="unknown" if exc.result_unknown or chain.unknown_submission() else "failed")
        return
    candidate = chain.delivery_candidate(0, chain.delivery_dimensions())
    choice = chain.selections.get(0)
    if (
        candidate is None
        or choice is None
        or choice.mode != "passed"
        or candidate.review is None
        or candidate.review.evidence is None
        or not await asyncio.to_thread(review_media_intact, candidate.review.evidence, candidate.path)
    ):
        await _finish(work, state="unknown" if chain.unknown_submission() else "failed")
        return
    try:
        async with SESSION_LOCAL() as db:
            await _check_image(db, work, plan, share_lock=True)
            await settle_episode_media(db, work.user_id, work.id, asset_path=candidate.path)
            await db.commit()
    except OSError:
        logger.warning("Episode image attachment deferred", extra={"slot_id": work.id}, exc_info=True)


async def _start_video(work: EpisodeMediaWork, plan: _FrozenEpisodeMedia) -> None:
    require_new_generation_call(work.user_id)
    if plan.duration is None or plan.resolution is None:
        raise ValueError("原近况缺少视频规格")
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, work.user_id, share_lock=True):
            raise GenerationWorkPaused
        await _check_identity(db, work, plan)
        current = await read_episode_media_work(db, work.user_id, work.id, lock=True)
        if current is None or current.job_id is not None or current.state in _TERMINAL_STATES:
            return
        job = await prepare_video_job(
            db,
            user_id=work.user_id,
            task=_TASK,
            session_id=None,
            asset_directory=_directory(work),
            prompt=plan.prompt,
            goal=plan.goal,
            duration=plan.duration,
            resolution=plan.resolution,
            aspect_ratio=_VIDEO_ASPECT_RATIO,
            reference_images=plan.references,
            reference_guidance=plan.reference_guidance,
            performance_context=plan.performance_context,
            portrait_reference=plan.portrait_path,
            identity_reference_path=plan.identity_path,
            identity=plan.identity,
            identity_reference_role=plan.reference_role,
            include_upscaled=False,
        )
        await write_episode_media_progress(db, work.user_id, work.id, state="running", job_id=job.id)
        await wait_for_media(
            db,
            user_id=work.user_id,
            owner_kind=VIDEO_CONTINUATION_KIND,
            owner_job_id=job.id,
            dependency_job_id=job.id,
            operation_id=f"life-episode-video:{work.id}",
            automatic=True,
            context={"slot_id": work.id},
            client_visible=False,
        )
        await db.commit()
    try:
        submitted = await submit_video_job(work.user_id, job.id)
    except OSError:
        logger.warning("Episode video submission deferred", extra={"slot_id": work.id}, exc_info=True)
        return
    await _receive_video(work, submitted)


def _poster_bytes(path: Path) -> bytes:
    with Image.open(BytesIO(extract_desktop_first_frame(path))) as frame, BytesIO() as output:
        frame.thumbnail((720, 720), Image.Resampling.LANCZOS)
        frame.save(output, format="WEBP", quality=83)
        return output.getvalue()


async def _receive_video(work: EpisodeMediaWork, job: VideoJobSnapshot | None) -> None:
    if job is None or job.cleanup_requested_at or job.status in {"failed", "discarded", "cancelled"}:
        await _finish(work, state="failed")
        return
    if job.status == "result_unknown":
        if work.state != "unknown":
            await _finish(work, state="unknown")
        return
    if job.status != "succeeded":
        return
    path = resolve_asset_reference(job.video_url) if job.video_url else None
    if path is None or job.selection is None or job.selection.mode != "passed":
        await _finish(work, state="failed")
        return
    try:
        data = await run_blocking(_poster_bytes, path[0])
        poster = await save_companion_asset_async(
            data, user_id=work.user_id, label="episode_poster", ext="webp", directory=_directory(work)
        )
        async with SESSION_LOCAL() as db:
            if not await user_is_active(db, work.user_id, share_lock=True):
                return
            accepted = await accept_checked_video(db, work.user_id, job.id)
            if accepted:
                await settle_episode_media(db, work.user_id, work.id, asset_path=job.video_url, poster_path=poster)
            else:
                await settle_episode_media(db, work.user_id, work.id, state="failed")
            await db.commit()
    except OSError:
        logger.warning("Episode video delivery deferred", extra={"slot_id": work.id}, exc_info=True)


async def _run_episode_media(user_id: int, slot_id: int) -> None:
    if is_user_in_maintenance(user_id):
        return
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id):
            return
        work = await read_episode_media_work(db, user_id, slot_id)
        job = await get_job(db, work.job_id, user_id) if work and work.job_id is not None else None
    if work is None or work.state in _TERMINAL_STATES:
        return
    with asset_write_context(inherit=False):
        try:
            if work.job_id is not None:
                await _receive_video(work, job)
            else:
                plan = _FrozenEpisodeMedia.model_validate(work.plan)
                if plan.kind == "image":
                    await _produce_image(work, plan)
                else:
                    await _start_video(work, plan)
        except GenerationWorkPaused:
            return
        except (MissingLlmConfigError, OSError, VideoProcessError, ValueError) as exc:
            logger.warning(
                "Episode media could not be delivered",
                extra={"user_id": user_id, "slot_id": slot_id, "reason": type(exc).__name__},
            )
            await _finish(work, state="failed")


def _schedule_episode_media(user_id: int, slot_id: int) -> None:
    _TASKS.schedule(slot_id, partial(_run_episode_media, user_id, slot_id), user_id=user_id)


async def continue_episode_video(db: AsyncSession, wait: MediaWait) -> dict | None:
    """原视频终态通知唤醒所属近况，封面解码在事务外完成。"""
    slot_id = int(wait.context_json["slot_id"])
    work = await read_episode_media_work(db, wait.user_id, slot_id)
    if work is None or work.job_id != wait.dependency_job_id or work.state in _TERMINAL_STATES:
        return {"state": "closed"}
    video = await get_job(db, wait.dependency_job_id, wait.user_id)
    if video is None or video.status in _VIDEO_TERMINAL:
        _schedule_episode_media(wait.user_id, slot_id)
    return None


async def recover_episode_media(user_id: int | None = None) -> None:
    async with SESSION_LOCAL() as db:
        pending = await pending_episode_media_work(db, user_id)
    for owner, slot_id in pending:
        if not is_user_in_maintenance(owner):
            _schedule_episode_media(owner, slot_id)


async def wake_episode_media(user_id: int) -> None:
    await recover_episode_media(user_id)


async def drain_episode_media() -> None:
    await _TASKS.drain()
