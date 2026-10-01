"""角色视频片段处理：切分重置时间轴 → 统一画布与脚底锚点 → 透明 VP9 编码 → 封面与命中遮罩。交付格式固定 WebM/VP9+Alpha（yuva420p）、无音轨；处理参数由代码构造，外部输入只提供路径与受校验的整数。"""

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from components import get_logger
from PIL import Image

from services.infrastructure.assets import compute_file_sha256

from .ffmpeg import (
    VideoProbe,
    VideoProcessError,
    alpha_input_args,
    ffmpeg_stdout,
    probe_alpha_side_data,
    probe_video,
    run_ffmpeg,
)

logger = get_logger(__name__)

# 交付格式、源文件限额与上传导入的画布上限
TARGET_EXT = "webm"
MAX_SOURCE_BYTES = 96 * 1024 * 1024
MAX_CANVAS_WIDTH = 1024
MAX_CANVAS_HEIGHT = 1024

# VP9 + Alpha 编码参数：yuva420p；auto-alt-ref 关闭否则透明帧被不透明 alt-ref 帧破坏；alpha_mode=1 写入容器元数据供 WebM 解码器启用透明通道
VP9_ALPHA_ENCODE_ARGS: tuple[str, ...] = (
    "-c:v",
    "libvpx-vp9",
    "-pix_fmt",
    "yuva420p",
    "-auto-alt-ref",
    "0",
    "-metadata:s:v:0",
    "alpha_mode=1",
    "-deadline",
    "good",
    "-cpu-used",
    "2",
    "-crf",
    "20",
    "-b:v",
    "0",
)

# 命中遮罩与交付帧率一致，降采样到低分辨率网格，alpha 阈值以上视为身体。
HITMASK_FPS = 24
HITMASK_GRID_W = 32
HITMASK_GRID_H = 32
HITMASK_ALPHA_THRESHOLD = 24


@dataclass(frozen=True)
class ClipProcessResult:
    sha256: str
    frames: int
    duration_ms: int
    width: int
    height: int


def _native_canvas_size(width: int, height: int, canvas_w: int, canvas_h: int) -> tuple[int, int]:
    """容纳源像素的最小同宽高比偶数画布，供生成素材仅补边而不重采样。"""
    divisor = math.gcd(canvas_w, canvas_h)
    unit_w, unit_h = canvas_w // divisor, canvas_h // divisor
    multiple = max((width + unit_w - 1) // unit_w, (height + unit_h - 1) // unit_h)
    if (unit_w * multiple) % 2 or (unit_h * multiple) % 2:
        multiple += 1
    return unit_w * multiple, unit_h * multiple


def _canvas_filter(canvas_w: int, canvas_h: int, *, preserve_resolution: bool = False) -> str:
    """生成素材仅补边，导入素材等比缩放；水平居中、底边对齐画布。
    脚底锚点即画布底边；不逐帧紧裁，避免角色抖动。"""
    scale = "" if preserve_resolution else f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
    return f"format=rgba,{scale}pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih):color=black@0,fps=24,format=rgba"


def prepare_action_clip(
    src: Path,
    dst: Path,
    *,
    canvas_w: int,
    canvas_h: int,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    preserve_resolution: bool = False,
) -> ClipProcessResult:
    """切分（可选区间）、重置时间轴、统一画布与帧率并编码为透明 WebM。
    输出从 0 开始的新时间轴；时长读取编码产物，帧数按实际解码计数。
    输入与输出都须带透明通道：不透明源会被转成不透明矩形而非透明角色，必须拒绝。"""
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160 or probe.fps > 120:
        raise VideoProcessError("源片段分辨率或帧率超出处理上限")
    if not probe.has_alpha:
        raise VideoProcessError("源片段缺少透明通道，请提供透明背景的素材")
    if canvas_w <= 0 or canvas_h <= 0 or canvas_w % 2 or canvas_h % 2:
        raise VideoProcessError("视频画布必须为正偶数尺寸")
    if preserve_resolution:
        canvas_w, canvas_h = _native_canvas_size(probe.width, probe.height, canvas_w, canvas_h)
    for value in (start_seconds, end_seconds):
        if value is not None and not math.isfinite(value):
            raise VideoProcessError("动作区间必须为有限数值")

    start = max(0.0, start_seconds) if start_seconds is not None else None
    end = min(probe.duration_seconds, end_seconds) if end_seconds is not None else None
    if (end if end is not None else probe.duration_seconds) <= (start or 0.0):
        raise VideoProcessError("动作区间终点必须晚于起点")

    args: list[str] = []
    # 输入侧 seek 后时间轴归零，区间终点必须换算成输出时长（-to 会按归零后的时间轴解释）。
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += [*alpha_input_args(probe), "-i", str(src)]
    if end is not None:
        args += ["-t", f"{end - (start or 0.0):.3f}"]
    args += ["-an", "-sn", "-dn", "-vf", _canvas_filter(canvas_w, canvas_h, preserve_resolution=preserve_resolution)]
    args += [*VP9_ALPHA_ENCODE_ARGS, str(dst)]

    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, label="处理")
    return _verify_output(dst)


def _verify_output(dst: Path) -> ClipProcessResult:
    out = probe_video(dst)
    if out.codec_name != "vp9":
        raise VideoProcessError("视频编码产物异常", internal=f"codec={out.codec_name}")
    if not out.has_alpha or not probe_alpha_side_data(dst):
        # 容器声明单独不能证明真实 alpha。
        raise VideoProcessError("视频缺少透明通道，无法作为角色片段使用", internal=f"pix_fmt={out.pix_fmt}")
    invalid_alpha = "透明片段缺少有效前景或透明背景"
    alpha = ffmpeg_stdout(
        [
            *alpha_input_args(out),
            "-i",
            str(dst),
            "-vf",
            "format=rgba,alphaextract,scale=32:32",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        failure_message=invalid_alpha,
    )
    frame_size = 32 * 32
    if not alpha or len(alpha) % frame_size or min(alpha) > 8 or max(alpha) < 240:
        raise VideoProcessError(invalid_alpha)
    return ClipProcessResult(
        sha256=compute_file_sha256(dst),
        frames=len(alpha) // frame_size,
        duration_ms=round(out.duration_seconds * 1000),
        width=out.width,
        height=out.height,
    )


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


def extract_cover(src: Path, *, canvas_w: int, canvas_h: int) -> bytes:
    """交付片段首帧的透明 WebP 字节。"""
    return _frame_webp(src, probe_video(src), canvas_w=canvas_w, canvas_h=canvas_h, at_seconds=0.0)


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


def build_hitmask(src: Path, *, canvas_w: int, canvas_h: int) -> list[list[int]]:
    """逐帧生成低分辨率 alpha 命中遮罩：返回 [frame][row] 的列位行。
    客户端按呈现时间取当前帧查表，经容器变换还原为屏幕坐标。

    WebM 通过 libvpx 解码，采样时间与最终交付片段一致。"""
    probe = probe_video(src)
    args = [
        *alpha_input_args(probe),
        "-i",
        str(src),
        "-vf",
        f"{_canvas_filter(canvas_w, canvas_h)},scale={HITMASK_GRID_W}:{HITMASK_GRID_H},format=rgba",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-",
    ]
    raw = ffmpeg_stdout(args, failure_message="命中遮罩生成失败")
    frame_bytes = HITMASK_GRID_W * HITMASK_GRID_H * 4
    if len(raw) < frame_bytes:
        raise VideoProcessError("命中遮罩生成失败", internal=f"short output {len(raw)}")

    mask: list[list[int]] = []
    for sample in range(len(raw) // frame_bytes):
        offset = sample * frame_bytes
        grid_rows: list[int] = []
        for row in range(HITMASK_GRID_H):
            row_bits = 0
            for col in range(HITMASK_GRID_W):
                alpha = raw[offset + (row * HITMASK_GRID_W + col) * 4 + 3]
                if alpha >= HITMASK_ALPHA_THRESHOLD:
                    row_bits |= 1 << col
            grid_rows.append(row_bits)
        mask.append(grid_rows)
    return mask
