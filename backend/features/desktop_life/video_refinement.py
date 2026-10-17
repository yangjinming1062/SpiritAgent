"""视频微调的素材检查、供应商规划与参考请求装配。"""

import asyncio
import math
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from infrastructure.ai.llm_client import build_provider, resolve_provider_chain, resolve_vision_chain
from infrastructure.ai.providers.base import ProviderError, VideoGenProvider, VideoGenRequest, VideoReferenceMetadata
from infrastructure.ai.selection import ProviderSelection, has_provider_selection, order_provider_chain
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import build_data_uri, resolve_asset_reference, signed_companion_asset_url
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.media_geometry import MAX_DESKTOP_CROP_FRACTION, crop_fraction
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video, run_ffmpeg
from PIL import Image

from features.desktop_life.prompts import DESKTOP_VIDEO_REFINE_TEMPLATE
from features.desktop_life.repository import DesktopVideoStateError
from features.desktop_life.schemas import DesktopVideoProgress
from features.desktop_life.video_planning import (
    DesktopVideoPlan,
    DesktopVideoResolution,
    freeze_desktop_video_plans,
    nearest_desktop_video_resolution,
)
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
    ratios: list[tuple[float, str]] = []
    for ratio in provider.aspect_ratios:
        native = Fraction(ratio.replace(":", "/"))
        loss = crop_fraction(native.numerator, native.denominator, target.numerator, target.denominator)
        if loss <= MAX_DESKTOP_CROP_FRACTION + 1e-9:
            ratios.append((loss, ratio))
    return min(ratios, key=lambda item: item[0])[1] if ratios else None


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
    target_width: int,
    target_height: int,
    geometry_mode: Literal["target", "keep"],
    selection: ProviderSelection | None = None,
    feedback: str = "",
) -> tuple[int, list[FrozenMediaProvider]]:
    try:
        metadata = await asyncio.to_thread(inspect_desktop_video_reference, data, extension)
    except (OSError, ValueError, VideoProcessError) as exc:
        raise DesktopVideoStateError(str(exc) if isinstance(exc, VideoProcessError) else "参考视频无法读取") from exc
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.desktop_life)
        configs = order_provider_chain(chain, selection)
        if not await resolve_vision_chain(db, user_id, task=AiTaskKey.desktop_life):
            raise DesktopVideoStateError("请先配置视觉模型")
    reference = (
        SETTINGS.public_base_url.rstrip("/") + "/api/companion/asset/reference.mp4"
        if SETTINGS.public_base_url.strip()
        else build_data_uri(data, "video/quicktime" if metadata.extension == "mov" else "video/mp4")
    )
    errors: list[str] = []

    def resolution_for(provider: VideoGenProvider, seconds: int, ratio: str) -> DesktopVideoResolution | None:
        choice = nearest_desktop_video_resolution(
            provider,
            duration=seconds,
            native_ratio=ratio,
            delivery_ratio=aspect_ratio,
            target_width=target_width,
            target_height=target_height,
            geometry_mode=geometry_mode,
            reference_videos=True,
        )
        if choice is not None:
            try:
                provider.validate_request(_request(feedback, ratio, seconds, choice.resolution, reference))
            except ProviderError as exc:
                errors.append(str(exc))
                return None
        return choice

    plans: list[DesktopVideoPlan] = []
    for config in configs:
        provider = build_provider(config, VideoGenProvider)
        if not provider.supports_reference_videos:
            continue
        try:
            provider.validate_reference_video(metadata)
            ratio = reference_video_ratio(provider, aspect_ratio)
            if ratio is None:
                raise DesktopVideoStateError(f"当前视频模型不支持 {aspect_ratio} 输出画幅，请更换视频模型或保留原片")
            duration = next(
                (
                    value
                    for value in sorted(provider.durations or ())
                    if value >= math.ceil(metadata.duration_seconds)
                    and provider.available_resolutions(duration=value, reference_videos=True)
                ),
                None,
            )
            if duration is None:
                continue
            choice = resolution_for(provider, duration, ratio)
        except (ProviderError, DesktopVideoStateError) as exc:
            errors.append(str(exc))
            continue
        if choice is not None:
            plans.append(DesktopVideoPlan(provider, ratio, duration, choice))
    result = freeze_desktop_video_plans(
        plans,
        preserve_order=has_provider_selection(chain, selection),
        resolution_for=resolution_for,
    )
    if result is None:
        raise DesktopVideoStateError(errors[0] if errors else "请在桌景视频调用链配置支持视频参考输入的模型")
    try:
        await asyncio.to_thread(_validate_reference_decoding, data, metadata.extension)
    except VideoProcessError as exc:
        raise DesktopVideoStateError(str(exc)) from exc
    return result


async def desktop_video_refinement_request(
    progress: DesktopVideoProgress,
    provider: VideoGenProvider,
    resolution: str,
    native_ratio: str,
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
    if native_ratio not in provider.aspect_ratios:
        raise DesktopVideoStateError("视频模型的冻结画幅已不再可用，请重新选择模型")
    if SETTINGS.public_base_url.strip():
        url = signed_companion_asset_url(refinement.video_path, ttl_seconds=24 * 60 * 60)
        if url is None:
            raise DesktopVideoStateError("参考视频无法读取")
        reference = SETTINGS.public_base_url.strip().rstrip("/") + url
    else:
        reference = build_data_uri(data, resolved[1])
    request = _request(progress.feedback, native_ratio, progress.duration_seconds, resolution, reference)
    provider.validate_request(request)
    return request
