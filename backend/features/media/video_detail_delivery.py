"""以原表演制作角色局部。各阶段先保存真实输出，再释放可恢复的供应商句柄。"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from functools import partial
from io import BytesIO
from typing import Literal

from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.providers.base import ProviderConfig, VideoGenRequest
from infrastructure.ai.providers.local.video import LocalVideoGenProvider, local_video_frame_count
from infrastructure.assets import asset_store
from infrastructure.assets.image_references import owned_image_path
from infrastructure.assets.media_download import download_media_result
from infrastructure.async_io import run_blocking
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.video_processing.face_performance import (
    compose_face_performance,
    performance_anchors,
    track_performance,
)
from infrastructure.video_processing.ffmpeg import VideoProcessError, VideoToolUnavailableError
from PIL import Image
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call

from features.jobs.service import bind_job_assets

from .chain import MediaChainState, media_failure_reason, resolve_frozen_media_provider, video_provider_failure_reason
from .video_queue.contracts import VideoQueueInfo, VideoQueueOwner, VideoQueueStoppedError
from .video_queue.service import (
    acknowledge_video_attempt,
    enqueue_video_attempt,
    find_video_attempt,
    wait_video_attempt,
)
from .video_results import download_and_store_video

logger = get_logger(__name__)


class FacePerformanceRejected(ValueError):
    """局部不可用时不把工作素材作为成片交给用户。"""

    def __init__(self, message: str, reason: str = "quality_failed", *, can_fallback: bool = True) -> None:
        super().__init__(message)
        self.reason = reason
        self.can_fallback = can_fallback


async def freeze_face_refinement(chain: MediaChainState, index: int, config: ProviderConfig) -> bool:
    if not chain.refine_character_faces or config.provider_name != "local" or index in chain.face_refinement_by_attempt:
        return False
    chain.face_refinement_by_attempt[index] = await LocalVideoGenProvider(config).supports_face_refinement()
    return True


def _split_guides(data: bytes, count: int) -> tuple[bytes, ...]:
    with Image.open(BytesIO(data)) as atlas:
        if atlas.format != "PNG" or atlas.size != (768 * count, 768):
            raise FacePerformanceRejected("局部导引图与原表演帧数不匹配")
        result = []
        for n in range(count):
            with atlas.crop((n * 768, 0, (n + 1) * 768, 768)) as guide, BytesIO() as buffer:
                guide.save(buffer, format="PNG")
                result.append(buffer.getvalue())
        return tuple(result)


async def store_performance_video(
    source_url: str,
    *,
    owner: VideoQueueOwner,
    chain: MediaChainState,
    generation_id: str,
    directory: str,
    portrait: str | None,
    goal: str,
    duration: int,
    keep_first: bool,
    keep_last: bool,
    allow_new: bool,
    save: Callable[[MediaChainState], Awaitable[None]],
    guard: Callable[[], Awaitable[None]],
    portrait_hash: str | None = None,
    on_queue: Callable[[str, VideoQueueInfo | None], Awaitable[None]] | None = None,
) -> str:
    index = chain.current_attempt_index()
    frozen = chain.providers[index]
    keys = {"user_id": owner.user_id, "job_id": owner.job_id, "attempt": index, "directory": directory}
    store_video = partial(
        download_and_store_video,
        guard=guard,
        service_base_url=frozen.base_url,
        **keys,
    )
    if not chain.refine_character_faces or not portrait or frozen.provider != "local":
        return await store_video(source_url, generation_id=generation_id)
    final = asset_store.video_job_asset_path(generation_id=generation_id, **keys)
    if asset_store.resolve_asset_reference(final):
        return final

    # 旧在途任务若已登记局部阶段，仍沿它原有的制作计划接收。
    anchor_index, motion_index = -(2 * index + 1), -(2 * index + 2)
    if index not in chain.face_refinement_by_attempt:
        if chain.face_request_id or chain.face_motion_source or await find_video_attempt(owner, anchor_index, frozen):
            chain.face_refinement_by_attempt[index] = True
        else:
            config = await resolve_frozen_media_provider(owner.user_id, "video_gen", frozen)
            if config is None:
                chain.face_refinement_by_attempt[index] = False
            else:
                await freeze_face_refinement(chain, index, config)
        await save(chain)
    if not chain.face_refinement_by_attempt[index]:
        return await store_video(source_url, generation_id=generation_id)

    def stage_id(stage: str) -> str:
        return hashlib.sha256(f"{generation_id}:{index}:{stage}".encode()).hexdigest()[:32]

    async def protect(*paths: str) -> None:
        async with SESSION_LOCAL() as db:
            await bind_job_assets(db, owner.user_id, owner.job_id, set(paths), "input")
            await db.commit()

    await guard()
    draft = await store_video(source_url, generation_id=stage_id("motion"))
    await protect(draft)
    source = asset_store.resolve_asset_reference(draft)[0]
    original = asset_store.resolve_asset_reference(owned_image_path(portrait, owner.user_id))
    if not original:
        raise ValueError("冻结的角色依据无法读取")
    identity = await asyncio.to_thread(original[0].read_bytes)
    if portrait_hash and hashlib.sha256(identity).hexdigest() != portrait_hash:
        raise FacePerformanceRejected("原身份依据已变化", "identity_changed", can_fallback=False)

    async def save_draft() -> str:
        if chain.face_motion_source is not None:
            chain.face_motion_source = None
            await save(chain)
        await guard()
        return await asset_store.save_video_job_asset_async(
            await asyncio.to_thread(source.read_bytes), generation_id=generation_id, **keys
        )

    try:
        track = await run_blocking(track_performance, source, identity, frame_count=local_video_frame_count(duration))
    except VideoToolUnavailableError:
        raise
    except VideoProcessError as exc:
        raise FacePerformanceRejected(str(exc)) from exc
    if track is None:
        if chain.face_request_id or await find_video_attempt(owner, anchor_index, frozen):
            raise VideoToolUnavailableError("无法恢复已登记的面部轨迹")
        return await save_draft()

    face_feedback = ""
    if chain.is_correction_attempt(index):
        for candidate in reversed(chain.candidates):
            previous = candidate.review
            if candidate.attempt == index - 1 and previous and previous.face and previous.face.verdict != "pass":
                face_feedback = (
                    "依据原头像与当前画格核对上一版的面部观察。仅修正此角度可见的问题，当前表演以本段底片为准。"
                    + previous.face.reason
                )
                break

    async def receive(
        stage: Literal["anchors", "motion"], slot: int, token: str | None, references: tuple[str, ...]
    ) -> tuple[str, str] | None:
        if token is None:
            await guard()
            require_new_generation_call(owner.user_id)
            request = VideoGenRequest(
                prompt="根据导引画面连续拍摄同一角色的面部局部。按实拍画格中的视线、眼睑开合、嘴形和头部角度衔接，"
                "保持原有的焦点、光照和透视。以下是这个局部所属的全景活动："
                + goal
                + ("\n" + face_feedback if face_feedback else ""),
                duration=duration,
                resolution="768P",
                aspect_ratio="1:1",
                noise_seed=chain.local_seed(index),
                reference_images=references,
                face_guide_frames=track.frames,
                face_stage=stage,
                face_repair_observation=face_feedback or None,
            )
            token = await enqueue_video_attempt(owner, frozen, slot, request)
        chain.face_request_id = token
        await save(chain)
        try:
            status = await wait_video_attempt(token, on_queue=partial(on_queue, token) if on_queue else None)
        except (LLMRuntimeError, VideoQueueStoppedError) as exc:
            reason, fallback = media_failure_reason(exc)
            if reason == "result_unknown":
                # 该句柄仍由持久队列跟踪；重新接收时只能查询原尝试。
                raise FacePerformanceRejected(str(exc), reason, can_fallback=False) from exc
            failure = FacePerformanceRejected(str(exc), reason, can_fallback=fallback)
            use_draft = fallback
        else:
            if status.status == "succeeded" and status.download_url:
                return token, status.download_url
            reason = video_provider_failure_reason(status.error)
            failure = FacePerformanceRejected(
                status.error or "局部面部制作未完成", reason, can_fallback=reason != "content_policy_blocked"
            )
            use_draft = status.status == "failed" and reason == "provider_failed"
        await finish(token)
        if use_draft:
            logger.warning(
                "Face refinement failed; reviewing the source clip",
                extra={"job_id": owner.job_id, "face_stage": stage, "reason": reason},
            )
            return None
        raise failure

    async def finish(token: str | None) -> None:
        if token:
            await acknowledge_video_attempt(token)
            if chain.face_request_id == token:
                chain.face_request_id = None
                await save(chain)

    async def freeze_images(images: tuple[bytes, ...], label: str) -> tuple[str, ...]:
        paths = [
            await asset_store.save_image_chain_input_asset_async(
                data,
                user_id=owner.user_id,
                generation_id=stage_id("guides"),
                label=f"{label}{n}",
                ext="png",
                directory=directory,
            )
            for n, data in zip(track.frames, images, strict=True)
        ]
        await protect(*paths)
        return tuple(paths)

    patch = asset_store.video_job_asset_path(generation_id=stage_id("face"), **keys)
    local_patch = asset_store.resolve_asset_reference(patch)
    if not local_patch:
        atlas_keys = {
            "user_id": owner.user_id,
            "generation_id": stage_id("guides"),
            "label": "complete",
            "ext": "png",
            "directory": directory,
        }
        atlas = asset_store.image_chain_input_asset_path(**atlas_keys)
        local_atlas = asset_store.resolve_asset_reference(atlas)
        anchor_token = await find_video_attempt(owner, anchor_index, frozen)
        if local_atlas:
            data = await asyncio.to_thread(local_atlas[0].read_bytes)
        else:
            if anchor_token is None and not allow_new:
                raise GenerationWorkPaused
            sources = (
                ()
                if anchor_token
                else await freeze_images(await run_blocking(performance_anchors, source, track), "source")
            )
            anchors = await receive("anchors", anchor_index, anchor_token, (portrait, *sources))
            if anchors is None:
                return await save_draft()
            anchor_token, url = anchors
            data = await download_media_result(
                url, max_bytes=REMOTE_ASSET_DOWNLOAD_MAX_BYTES, service_base_url=frozen.base_url
            )
        try:
            guides = await asyncio.to_thread(_split_guides, data, len(track.frames))
        except (OSError, ValueError) as exc:
            await finish(anchor_token)
            raise FacePerformanceRejected("面部导引图无法使用") from exc
        if not local_atlas:
            await guard()
            await asset_store.save_image_chain_input_asset_async(data, **atlas_keys)
        await protect(atlas)
        await finish(anchor_token)
        motion_token = await find_video_attempt(owner, motion_index, frozen)
        if motion_token is None and not allow_new:
            raise GenerationWorkPaused
        refs = () if motion_token else await freeze_images(guides, "anchor")
        motion = await receive("motion", motion_index, motion_token, refs)
        if motion is None:
            return await save_draft()
        motion_token, url = motion
        await store_video(url, generation_id=stage_id("face"))
        local_patch = asset_store.resolve_asset_reference(patch)
    else:
        motion_token = await find_video_attempt(owner, motion_index, frozen)
    await protect(patch)
    chain.face_motion_source = draft
    await save(chain)
    await finish(motion_token)
    try:
        data = await run_blocking(
            compose_face_performance, source, local_patch[0], track, keep_first=keep_first, keep_last=keep_last
        )
    except VideoToolUnavailableError:
        raise
    except VideoProcessError:
        logger.warning("Face refinement could not be composed; reviewing the source clip", exc_info=True)
        return await save_draft()
    await guard()
    return await asset_store.save_video_job_asset_async(data, generation_id=generation_id, **keys)
