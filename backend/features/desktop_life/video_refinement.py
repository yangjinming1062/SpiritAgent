"""视频微调的素材检查、供应商规划与参考请求装配。"""

import asyncio
import math
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.ai.llm_client import build_provider, resolve_provider_chain, resolve_vision_chain
from infrastructure.ai.providers.base import ProviderError, VideoGenProvider, VideoGenRequest, VideoReferenceMetadata
from infrastructure.ai.selection import ProviderSelection, order_provider_chain
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import build_data_uri, resolve_asset_reference, signed_companion_asset_url
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video, run_ffmpeg
from PIL import Image

from features.desktop_life.prompts import DESKTOP_VIDEO_REFINE_TEMPLATE
from features.desktop_life.repository import DesktopVideoStateError
from features.desktop_life.schemas import DesktopVideoProgress
from features.media.chain import FrozenMediaProvider


def inspect_desktop_video_reference(data: bytes, extension: str) -> VideoReferenceMetadata:
    extension = extension.lower().lstrip(".")
    if extension not in {"mp4", "mov"}:
        raise DesktopVideoStateError("视频微调仅支持 MP4 或 MOV 参考视频")
    with TemporaryDirectory(prefix="desktop-video-reference-") as directory:
        path = Path(directory) / f"source.{extension}"
        path.write_bytes(data)
        probe = probe_video(path)
        if not {"mov", "mp4"}.intersection(probe.format_name.split(",")):
            raise DesktopVideoStateError("视频微调仅支持 MP4 或 MOV 参考视频")
        with Image.open(BytesIO(extract_desktop_first_frame(path))) as image:
            width, height = image.size
        return VideoReferenceMetadata(
            size_bytes=len(data),
            extension=extension,
            video_codec=probe.codec_name,
            audio_codecs=probe.audio_codecs,
            duration_seconds=probe.duration_seconds,
            width=probe.width,
            height=probe.height,
            display_width=width,
            display_height=height,
            fps=probe.fps,
        )


def reference_video_ratio(provider: VideoGenProvider, aspect_ratio: str) -> str | None:
    """视频参考只采用明确画幅；图生视频的 adaptive 能力不代表任意参考输出画幅。"""
    target = Fraction(aspect_ratio.replace(":", "/"))
    return next(
        (
            ratio
            for ratio in provider.aspect_ratios
            if abs(float(Fraction(ratio.replace(":", "/")) / target) - 1) <= 0.01
        ),
        None,
    )


def _validate_reference_decoding(data: bytes, extension: str) -> None:
    with TemporaryDirectory(prefix="desktop-video-reference-") as directory:
        path = Path(directory) / f"source.{extension.lower().lstrip('.')}"
        path.write_bytes(data)
        run_ffmpeg(["-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"], label="参考视频解码检查")


def _request(prompt_feedback: str, ratio: str, seconds: int, resolution: str, reference: str) -> VideoGenRequest:
    return VideoGenRequest(
        prompt=DESKTOP_VIDEO_REFINE_TEMPLATE.format(
            feedback=prompt_feedback.strip() or "适配要求的画幅，保留原视频的主体、环境、活动过程与节奏。",
            aspect_ratio=ratio,
            duration_seconds=seconds,
        ),
        duration=seconds,
        resolution=resolution,
        reference_videos=(reference,),
        aspect_ratio=ratio,
    )


async def plan_desktop_video_refinement(
    user_id: int,
    data: bytes,
    extension: str,
    *,
    aspect_ratio: str,
    selection: ProviderSelection | None = None,
    feedback: str = "",
) -> tuple[int, list[FrozenMediaProvider]]:
    try:
        metadata = await asyncio.to_thread(inspect_desktop_video_reference, data, extension)
    except (OSError, ValueError, VideoProcessError) as exc:
        raise DesktopVideoStateError(str(exc) if isinstance(exc, VideoProcessError) else "参考视频无法读取") from exc
    async with SESSION_LOCAL() as db:
        configs = order_provider_chain(
            await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.desktop_life),
            selection,
        )
        if not await resolve_vision_chain(db, user_id, task=AiTaskKey.desktop_life):
            raise DesktopVideoStateError("请先配置视觉模型")
    reference = (
        SETTINGS.public_base_url.rstrip("/") + "/api/companion/asset/reference.mp4"
        if SETTINGS.public_base_url.strip()
        else build_data_uri(data, "video/quicktime" if metadata.extension == "mov" else "video/mp4")
    )
    seconds: int | None = None
    providers: list[FrozenMediaProvider] = []
    errors: list[str] = []
    for config in configs:
        provider = build_provider(config, VideoGenProvider)
        if not provider.supports_reference_videos:
            continue
        try:
            provider.validate_reference_video(metadata)
            ratio = reference_video_ratio(provider, aspect_ratio)
            if ratio is None:
                raise DesktopVideoStateError(f"当前视频模型不支持 {aspect_ratio} 输出画幅，请修改桌景目标或保留原片")
            duration = seconds or next(
                (
                    value
                    for value in sorted(provider.durations or ())
                    if value >= math.ceil(metadata.duration_seconds)
                    and provider.max_resolution(duration=value, reference_videos=True)
                ),
                None,
            )
            resolution = provider.max_resolution(duration=duration, reference_videos=True) if duration else None
            if duration is None or resolution is None:
                continue
            provider.validate_request(_request(feedback, ratio, duration, resolution, reference))
        except (ProviderError, DesktopVideoStateError) as exc:
            errors.append(str(exc))
            continue
        frozen = FrozenMediaProvider.from_config(config)
        frozen.video_resolution = resolution
        providers.append(frozen)
        seconds = duration
    if seconds is None or not providers:
        raise DesktopVideoStateError(errors[0] if errors else "请在桌景视频调用链配置支持视频参考输入的模型")
    try:
        await asyncio.to_thread(_validate_reference_decoding, data, metadata.extension)
    except VideoProcessError as exc:
        raise DesktopVideoStateError(str(exc)) from exc
    return seconds, providers


async def desktop_video_refinement_request(
    progress: DesktopVideoProgress,
    provider: VideoGenProvider,
    resolution: str,
) -> VideoGenRequest:
    refinement = progress.video_refinement
    if refinement is None:
        raise DesktopVideoStateError("视频微调参考缺失")
    resolved = resolve_asset_reference(refinement.video_path)
    if resolved is None:
        raise DesktopVideoStateError("参考视频无法读取")
    data = await asyncio.to_thread(resolved[0].read_bytes)
    extension = resolved[0].suffix.lstrip(".").lower()
    metadata = await asyncio.to_thread(inspect_desktop_video_reference, data, extension)
    provider.validate_reference_video(metadata)
    ratio = reference_video_ratio(provider, progress.aspect_ratio)
    if ratio is None:
        raise DesktopVideoStateError(f"当前视频模型不支持 {progress.aspect_ratio} 输出画幅")
    if SETTINGS.public_base_url.strip():
        url = signed_companion_asset_url(refinement.video_path, ttl_seconds=24 * 60 * 60)
        if url is None:
            raise DesktopVideoStateError("参考视频无法读取")
        reference = SETTINGS.public_base_url.strip().rstrip("/") + url
    else:
        reference = build_data_uri(data, resolved[1])
    request = _request(progress.feedback, ratio, progress.duration_seconds, resolution, reference)
    provider.validate_request(request)
    return request
