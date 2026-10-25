"""按视频原画幅抽取首、中、末帧，供媒体身份核查。"""

from io import BytesIO
from pathlib import Path

from PIL import Image

from infrastructure.video_processing.ffmpeg import (
    VideoProbe,
    VideoProcessError,
    alpha_input_args,
    ffmpeg_stdout,
    probe_video,
)


def _canvas_filter(canvas_w: int, canvas_h: int) -> str:
    """采样帧等比缩放并对齐画布，不裁切角色边缘。"""
    scale = f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
    return f"format=rgba,{scale}pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih):color=black@0,fps=24,format=rgba"


def _frame_webp(src: Path, probe: VideoProbe, *, canvas_w: int, canvas_h: int, at_seconds: float) -> bytes:
    """解码指定时刻的 RGBA 帧，由 Pillow 编码为无损透明 WebP。"""
    args = [
        "-ss",
        f"{max(0.0, at_seconds):.3f}",
        *alpha_input_args(probe),
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-an",
        "-vf",
        _canvas_filter(canvas_w, canvas_h),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-",
    ]
    frame = ffmpeg_stdout(args, failure_message="封面解码失败")
    if len(frame) != canvas_w * canvas_h * 4:
        raise VideoProcessError("封面解码失败", internal=f"unexpected frame size {len(frame)}")
    output = BytesIO()
    Image.frombytes("RGBA", (canvas_w, canvas_h), frame).save(output, format="WEBP", lossless=True)
    return output.getvalue()


def sample_key_frames(src: Path) -> list[bytes]:
    """按原画幅（边长上限 1024、取偶数）抽取首、中、末三帧透明 WebP；末帧前移 0.15 秒避开编码尾帧。"""
    probe = probe_video(src)
    scale = min(1.0, 1024 / max(probe.width, probe.height))
    canvas_w = max(2, round(probe.width * scale / 2) * 2)
    canvas_h = max(2, round(probe.height * scale / 2) * 2)
    duration = probe.duration_seconds
    return [
        _frame_webp(src, probe, canvas_w=canvas_w, canvas_h=canvas_h, at_seconds=second)
        for second in (0.0, duration / 2, max(0.0, duration - 0.15))
    ]
