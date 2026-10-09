"""完整环境视频的无声 MP4 交付，不执行透明化或人物裁切。"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from .ffmpeg import VideoProcessError, ffmpeg_stdout, probe_video, run_ffmpeg
from .process import extract_cover


@dataclass(frozen=True)
class DesktopVideoResult:
    width: int
    height: int
    duration_ms: int
    cover: bytes


@dataclass(frozen=True)
class DesktopVideoFrames:
    frames: tuple[bytes, ...]
    times_seconds: tuple[float, ...]


def sample_desktop_video_frames(src: Path) -> DesktopVideoFrames:
    """从已规范化的 24 fps 成品抽取首尾与三个中间帧，保留准确时刻。"""
    probe = probe_video(src)
    if abs(probe.fps - 24) > 0.01:
        raise VideoProcessError("桌面视频尚未规范化，无法检查循环接点")
    last_index = max(0, round(probe.duration_seconds * probe.fps) - 1)
    indices = sorted({round(last_index * fraction / 4) for fraction in range(5)})
    scale = min(1.0, 1024 / max(probe.width, probe.height))
    width = max(2, round(probe.width * scale / 2) * 2)
    height = max(2, round(probe.height * scale / 2) * 2)
    selector = "+".join(f"eq(n\\,{index})" for index in indices)
    raw = ffmpeg_stdout(
        [
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"select={selector},scale={width}:{height},format=rgb24",
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
    return DesktopVideoFrames(tuple(frames), tuple(index / probe.fps for index in indices))


def prepare_desktop_video(src: Path, dst: Path) -> DesktopVideoResult:
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160 or probe.fps > 120:
        raise VideoProcessError("桌面视频分辨率或帧率超出处理上限")
    if abs(probe.width / probe.height - 16 / 9) > 0.06:
        raise VideoProcessError("桌面视频画幅不是16:9，请重做")
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
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,fps=24",
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
    return DesktopVideoResult(output.width, output.height, round(output.duration_seconds * 1000), cover)
