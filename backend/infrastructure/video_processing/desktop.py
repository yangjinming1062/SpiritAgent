"""桌面影像视频校验与适配；原始上传保留，派生视频按目标画幅裁切。"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from infrastructure.media_geometry import centered_crop_box
from infrastructure.video_processing.ffmpeg import (
    VideoProcessError,
    ffmpeg_stdout,
    probe_video,
    run_ffmpeg,
    video_frame_times,
)


@dataclass(frozen=True)
class DesktopVideoResult:
    width: int
    height: int
    duration_ms: int
    cover: bytes
    first_frame: bytes
    fitted: bool = False


@dataclass(frozen=True)
class DesktopVideoFrames:
    frames: tuple[bytes, ...]
    times_seconds: tuple[float, ...]


def sample_desktop_video_frames(src: Path) -> DesktopVideoFrames:
    """按实际帧时间抽取首尾和中间帧，支持原始帧率、VFR、旋转和非方形像素。"""
    times = video_frame_times(src)
    first, last = times[0], times[-1]
    indices = sorted(
        {
            min(range(len(times)), key=lambda index: abs(times[index] - target))
            for target in (first + (last - first) * fraction / 4 for fraction in range(5))
        },
    )
    with Image.open(BytesIO(extract_desktop_first_frame(src))) as frame:
        scale = min(1.0, 1024 / max(frame.size))
        width, height = (max(2, round(value * scale / 2) * 2) for value in frame.size)
    selector = "+".join(f"eq(n\\,{index})" for index in indices)
    raw = ffmpeg_stdout(
        [
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"select={selector},scale={width}:{height},setsar=1,format=rgb24",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        failure_message="桌面视频采样检查失败",
    )
    frame_size = width * height * 3
    if len(raw) != frame_size * len(indices):
        raise VideoProcessError("桌面视频首尾帧无法完整解码")
    frames: list[bytes] = []
    for index in range(len(indices)):
        data = raw[index * frame_size : (index + 1) * frame_size]
        with Image.frombytes("RGB", (width, height), data) as frame, BytesIO() as output:
            frame.save(output, format="WEBP", lossless=True)
            frames.append(output.getvalue())
    return DesktopVideoFrames(tuple(frames), tuple(times[index] - first for index in indices))


def extract_desktop_first_frame(src: Path) -> bytes:
    """原分辨率解码实际首帧；FFmpeg 自动应用显示旋转，再按 SAR 还原显示比例。"""
    data = ffmpeg_stdout(
        [
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-vf",
            "scale=round(iw*max(sar\\,1)):round(ih/min(sar\\,1)),setsar=1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        failure_message="桌面视频首帧解码失败",
    )
    try:
        with Image.open(BytesIO(data)) as frame:
            frame.load()
            if frame.width < 1 or frame.height < 1:
                raise VideoProcessError("桌面视频首帧尺寸无效")
    except OSError as exc:
        raise VideoProcessError("桌面视频首帧解码失败") from exc
    return data


def _target_dimensions(aspect_ratio: str) -> tuple[int, int]:
    try:
        width, height = (int(value) for value in aspect_ratio.split(":"))
        if min(width, height) <= 0:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise VideoProcessError("桌面视频目标画幅无效") from exc
    return width, height


def _fit_filter(
    first_frame: bytes,
    aspect_ratio: str | None,
) -> tuple[str, tuple[int, int], bool]:
    with Image.open(BytesIO(first_frame)) as frame:
        width, height = frame.size
    if width * height > 3840 * 2160:
        raise VideoProcessError("桌面视频分辨率超出处理上限")
    target = _target_dimensions(aspect_ratio) if aspect_ratio else (width, height)
    try:
        crop_left, crop_top, crop_right, crop_bottom = centered_crop_box(
            width,
            height,
            *target,
            alignment=2,
        )
    except ValueError as exc:
        raise VideoProcessError("桌面视频画幅差异超过少量裁切范围，请重新调整") from exc
    output_width, output_height = crop_right - crop_left, crop_bottom - crop_top
    return (
        f"scale={width}:{height},setsar=1,crop={output_width}:{output_height}:{crop_left}:{crop_top}:exact=1",
        (output_width, output_height),
        (output_width, output_height) != (width, height),
    )


def _desktop_result(dst: Path, expected_size: tuple[int, int], *, fitted: bool) -> DesktopVideoResult:
    output = probe_video(dst)
    if (output.width, output.height) != expected_size:
        raise VideoProcessError("桌面视频处理后的画幅无效")
    run_ffmpeg(["-xerror", "-i", str(dst), "-map", "0:v:0", "-an", "-f", "null", "-"], label="桌面视频解码检查")
    first_frame = extract_desktop_first_frame(dst)
    with Image.open(BytesIO(first_frame)) as frame, BytesIO() as buffer:
        frame.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        frame.save(buffer, format="WEBP", quality=90)
        cover = buffer.getvalue()
    return DesktopVideoResult(
        output.width,
        output.height,
        round(output.duration_seconds * 1000),
        cover,
        first_frame,
        fitted,
    )


def prepare_desktop_video(
    src: Path,
    dst: Path,
    *,
    aspect_ratio: str | None = None,
) -> DesktopVideoResult:
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160 or probe.fps > 120:
        raise VideoProcessError("桌面视频分辨率或帧率超出处理上限")
    first_frame = extract_desktop_first_frame(src)
    video_filter, expected_size, fitted = _fit_filter(first_frame, aspect_ratio)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"{video_filter},fps=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        label="桌面视频处理",
    )
    return _desktop_result(dst, expected_size, fitted=fitted)
