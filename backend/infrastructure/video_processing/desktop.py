"""桌面生活视频处理：系统生成视频规范化，用户上传视频原样校验与封面生成。"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from infrastructure.video_processing.ffmpeg import (
    VideoProcessError,
    ffmpeg_stdout,
    probe_video,
    run_ffmpeg,
    video_frame_times,
)
from infrastructure.video_processing.process import extract_cover


@dataclass(frozen=True)
class DesktopVideoResult:
    width: int
    height: int
    duration_ms: int
    cover: bytes
    first_frame: bytes


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


def prepare_desktop_video(src: Path, dst: Path, *, aspect_ratio: str | None = None) -> DesktopVideoResult:
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160 or probe.fps > 120:
        raise VideoProcessError("桌面视频分辨率或帧率超出处理上限")
    first_frame = extract_desktop_first_frame(src)
    with Image.open(BytesIO(first_frame)) as frame:
        if aspect_ratio is not None:
            try:
                numerator, denominator = (int(value) for value in aspect_ratio.split(":"))
                expected = numerator / denominator
            except (ValueError, ZeroDivisionError) as exc:
                raise VideoProcessError("桌面视频目标画幅无效") from exc
            if expected <= 0 or abs(frame.width / frame.height / expected - 1) > 0.03:
                raise VideoProcessError("桌面视频画幅与已确认图片不一致，请重做")
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
            "scale=trunc(iw*max(sar\\,1)/2)*2:trunc(ih/min(sar\\,1)/2)*2,setsar=1,fps=24",
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
    output = probe_video(dst)
    run_ffmpeg(["-i", str(dst), "-map", "0:v:0", "-an", "-f", "null", "-"], label="桌面视频解码检查")
    cover = extract_cover(dst, canvas_w=output.width, canvas_h=output.height)
    return DesktopVideoResult(
        output.width,
        output.height,
        round(output.duration_seconds * 1000),
        cover,
        extract_desktop_first_frame(dst),
    )


def prepare_external_desktop_video(src: Path) -> DesktopVideoResult:
    """验证外部视频并生成缩略图；视频文件本身保持原字节、画幅、帧率和音轨。"""
    probe = probe_video(src)
    if probe.codec_name not in {"h264", "vp8", "vp9", "av1"}:
        raise VideoProcessError("桌面播放器不支持该视频编码")
    container = probe.format_name.split(",", 1)[0]
    if container not in {"mov", "mp4", "matroska", "webm"}:
        raise VideoProcessError("桌面播放器不支持该视频容器")
    if any(codec not in {"aac", "mp3", "opus", "vorbis"} for codec in probe.audio_codecs):
        raise VideoProcessError("桌面播放器不支持该音频编码")
    # Chromium 的 H.264 硬件/软件路径要求常见的 8-bit 4:2:0；10-bit、4:2:2
    # 和 4:4:4 即使能被 FFmpeg 解码，也不能保证桌面视频元素可播放。
    h264_profile = probe.profile.casefold()
    if probe.codec_name == "h264" and (
        probe.pix_fmt not in {"yuv420p", "yuvj420p"}
        or h264_profile in {"high 10", "high 4:2:2", "high 4:4:4 predictive"}
    ):
        raise VideoProcessError("桌面播放器不支持该 H.264 像素格式")
    if probe.codec_name in {"vp8", "vp9"} and container not in {"matroska", "webm"}:
        raise VideoProcessError("桌面播放器不支持该 VPx 视频容器")
    # 完整解码只用于确认文件可读，不生成新的交付视频。
    run_ffmpeg(
        ["-xerror", "-i", str(src), "-map", "0:v:0", "-f", "null", "-"],
        label="桌面视频解码检查",
    )
    first_frame = extract_desktop_first_frame(src)
    with Image.open(BytesIO(first_frame)) as frame, BytesIO() as output:
        width, height = frame.size
        frame.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        frame.save(output, format="WEBP", quality=90)
        cover = output.getvalue()
    return DesktopVideoResult(width, height, round(probe.duration_seconds * 1000), cover, first_frame)
