"""角色视频片段处理：切分重置时间轴 → 统一画布与脚底锚点 → 透明 VP9 编码 → 封面与命中遮罩。交付格式固定 WebM/VP9+Alpha（yuva420p）、无音轨；处理参数由代码构造，外部输入只提供路径与受校验的整数。"""

import hashlib
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from components import get_logger
from modules.companion import ABSOLUTE_MAX_DURATION_SECONDS
from PIL import Image

from .ffmpeg import (
    VideoProbe,
    VideoProcessError,
    _binary,
    _run,
    alpha_input_args,
    probe_alpha_side_data,
    probe_video,
    run_ffmpeg,
)

logger = get_logger(__name__)

# 交付常量（PIPELINE「透明化与一致性」）：主格式与画布上限
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canvas_filter(canvas_w: int, canvas_h: int) -> str:
    """统一画布：等比缩放放入画布、水平居中、脚底（底边）对齐 canvas 底部。
    脚底锚点即画布底边；不逐帧紧裁，避免角色抖动。"""
    return (
        f"format=rgba,scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih):color=black@0,"
        "fps=24,format=rgba"
    )


def prepare_action_clip(
    src: Path,
    dst: Path,
    *,
    canvas_w: int,
    canvas_h: int,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> ClipProcessResult:
    """切分（可选区间）、重置时间轴、统一画布与帧率并编码为透明 WebM。
    输出从 0 开始的新时间轴；帧数与时长以编码产物 ffprobe 复核为准。
    输入与输出都须带透明通道：不透明源会被转成不透明矩形而非透明角色，必须拒绝。"""
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160 or probe.fps > 120:
        raise VideoProcessError("源片段分辨率或帧率超出处理上限")
    if probe.duration_seconds > ABSOLUTE_MAX_DURATION_SECONDS * 4:
        raise VideoProcessError("源片段过长，请提供单个动作的短视频")
    if not probe.has_alpha:
        raise VideoProcessError("源片段缺少透明通道，请提供透明背景的素材")
    for value in (start_seconds, end_seconds):
        if value is not None and not math.isfinite(value):
            raise VideoProcessError("动作区间必须为有限数值")

    start = max(0.0, start_seconds) if start_seconds is not None else None
    end = min(probe.duration_seconds, end_seconds) if end_seconds is not None else None
    if start is not None and end is not None and end - start < 0.2:
        raise VideoProcessError("动作区间过短，请校准起止时间")
    if (end if end is not None else probe.duration_seconds) - (start or 0.0) > ABSOLUTE_MAX_DURATION_SECONDS:
        raise VideoProcessError("单动作最长 15 秒，请校准起止时间")

    args: list[str] = []
    # 输入侧 seek 后时间轴归零，区间终点必须换算成输出时长（-to 会按归零后的时间轴解释）。
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += [*alpha_input_args(probe), "-i", str(src)]
    if end is not None:
        args += ["-t", f"{end - (start or 0.0):.3f}"]
    args += ["-an", "-sn", "-dn", "-vf", _canvas_filter(canvas_w, canvas_h)]
    args += [*VP9_ALPHA_ENCODE_ARGS, str(dst)]

    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, label="处理")
    return _verify_output(dst)


def _verify_output(dst: Path) -> ClipProcessResult:
    out = probe_video(dst)
    if out.duration_seconds > ABSOLUTE_MAX_DURATION_SECONDS:
        raise VideoProcessError("动作产物超过 15 秒")
    if out.codec_name != "vp9":
        raise VideoProcessError("视频编码产物异常", internal=f"codec={out.codec_name}")
    if not out.has_alpha or not probe_alpha_side_data(dst):
        # 容器声明单独不能证明真实 alpha。
        raise VideoProcessError("视频缺少透明通道，无法作为角色片段使用", internal=f"pix_fmt={out.pix_fmt}")
    decoded = _run(
        [
            _binary("ffmpeg"),
            "-v",
            "error",
            *alpha_input_args(out),
            "-i",
            str(dst),
            "-vf",
            "fps=4,format=rgba,alphaextract,scale=32:32",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
    )
    if decoded.returncode or not decoded.stdout or min(decoded.stdout) > 8 or max(decoded.stdout) < 240:
        raise VideoProcessError("透明片段缺少有效前景或透明背景")
    return ClipProcessResult(
        sha256=_sha256(dst),
        frames=max(1, round(out.duration_seconds * out.fps)),
        duration_ms=round(out.duration_seconds * 1000),
    )


def _frame_webp(src: Path, probe: VideoProbe, *, canvas_w: int, canvas_h: int, at_seconds: float) -> bytes:
    """解码指定时刻的 RGBA 帧，由 Pillow 编码为无损透明 WebP。"""
    args = [
        _binary("ffmpeg"),
        "-v",
        "error",
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
    proc = _run(args)
    if proc.returncode or len(proc.stdout) != canvas_w * canvas_h * 4:
        raise VideoProcessError("封面解码失败", internal=proc.stderr.decode(errors="replace")[:2000])
    output = BytesIO()
    Image.frombytes("RGBA", (canvas_w, canvas_h), proc.stdout).save(output, format="WEBP", lossless=True)
    return output.getvalue()


def extract_cover(src: Path, *, canvas_w: int, canvas_h: int) -> bytes:
    """交付片段首帧的透明 WebP 字节。"""
    return _frame_webp(src, probe_video(src), canvas_w=canvas_w, canvas_h=canvas_h, at_seconds=0.0)


def sample_key_frames(src: Path) -> list[bytes]:
    """按原画幅（边长上限 1024、取偶数）抽取首、中、末三帧透明 WebP；末帧前移 0.15 秒避开编码尾帧。"""
    probe = probe_video(src)
    canvas_w = max(2, min(probe.width, 1024) // 2 * 2)
    canvas_h = max(2, min(probe.height, 1024) // 2 * 2)
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
    span = probe.duration_seconds
    samples = max(1, math.ceil(span * HITMASK_FPS))
    args = [
        *alpha_input_args(probe),
        "-i",
        str(src),
        "-vf",
        f"{_canvas_filter(canvas_w, canvas_h)},"
        f"trim=duration={span:.3f},scale={HITMASK_GRID_W}:{HITMASK_GRID_H},format=rgba",
        "-frames:v",
        str(samples),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-",
    ]
    proc = _run([_binary("ffmpeg"), "-v", "error", *args])
    if proc.returncode != 0:
        raise VideoProcessError("命中遮罩生成失败", internal=proc.stderr.decode("utf-8", "replace")[:2000])
    raw = proc.stdout
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
