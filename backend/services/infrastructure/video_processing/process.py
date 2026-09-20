"""角色视频片段处理：切分重置时间轴 → 统一画布与脚底锚点 → 透明 VP9 编码 → 封面与命中遮罩。

交付格式固定 WebM / VP9 + Alpha（yuva420p）、无音轨；编码验收以 probe 实际像素格式为准，
不以扩展名代替。处理参数由代码构造，外部输入只提供路径与受校验的整数。
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from components import get_logger

from .ffmpeg import VideoProcessError, probe_video, run_ffmpeg

logger = get_logger(__name__)

# 交付常量（PIPELINE §视频）：主格式与画布上限
TARGET_EXT = "webm"
MAX_CLIP_SECONDS = 12.0
MAX_SOURCE_BYTES = 96 * 1024 * 1024
MAX_CANVAS_WIDTH = 1024
MAX_CANVAS_HEIGHT = 1024

# 命中遮罩：每秒采样一帧，降采样到低分辨率网格（宽×高），alpha 阈值以上视为身体
HITMASK_GRID_W = 32
HITMASK_GRID_H = 32
HITMASK_ALPHA_THRESHOLD = 24


@dataclass(frozen=True)
class ClipProcessResult:
    path: Path
    sha256: str
    bytes: int
    frames: int
    duration_ms: int
    width: int
    height: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canvas_args(canvas_w: int, canvas_h: int, *, keep_source_alpha: bool) -> list[str]:
    """统一画布：等比缩放放入画布、水平居中、脚底（底边）对齐 canvas 底部。
    脚底锚点即画布底边；不逐帧紧裁，避免角色抖动。"""
    vf = (
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih),"
        "fps=24"
    )
    fmt = "rgba" if keep_source_alpha else "yuv420p"
    return ["-vf", f"{vf},format={fmt}"]


def prepare_action_clip(
    src: Path,
    dst: Path,
    *,
    canvas_w: int,
    canvas_h: int,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    require_alpha: bool = True,
) -> ClipProcessResult:
    """切分（可选区间）、重置时间轴、统一画布与帧率并编码为透明 WebM。
    输出从 0 开始的新时间轴；帧数与时长以编码产物 ffprobe 复核为准。
    require_alpha 同时守卫输入与输出：不透明源会被转成不透明矩形而非透明角色，必须拒绝。"""
    probe = probe_video(src)
    if probe.duration_seconds > MAX_CLIP_SECONDS * 4:
        raise VideoProcessError("源片段过长，请提供单个动作的短视频")
    if require_alpha and not probe.has_alpha:
        raise VideoProcessError("源片段缺少透明通道，请提供透明背景的素材")

    start = max(0.0, start_seconds) if start_seconds is not None else None
    end = min(probe.duration_seconds, end_seconds) if end_seconds is not None else None
    if start is not None and end is not None and end - start < 0.2:
        raise VideoProcessError("动作区间过短，请校准起止时间")

    args: list[str] = []
    # 准确切分用输出侧 seek（重编码场景无性能顾虑），时间轴随重编码自然归零。
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-an", "-sn", "-dn"]
    args += _canvas_args(canvas_w, canvas_h, keep_source_alpha=require_alpha)
    # VP9 + Alpha：yuva420p；auto-alt-ref 关闭，否则透明帧会被不透明 alt-ref 帧破坏；
    # alpha_mode=1 写入容器元数据，WebM 解码器（含 Chromium）据此启用透明通道。
    args += [
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuva420p",
        "-auto-alt-ref",
        "0",
        "-metadata:s:v:0",
        "alpha_mode=1",
        "-deadline",
        "realtime",
        "-cpu-used",
        "5",
        "-crf",
        "20",
        "-b:v",
        "0",
        str(dst),
    ]

    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, label="处理")
    return _verify_output(dst, require_alpha=require_alpha)


def _verify_output(dst: Path, *, require_alpha: bool) -> ClipProcessResult:
    out = probe_video(dst)
    if out.codec_name != "vp9":
        raise VideoProcessError("视频编码产物异常", internal=f"codec={out.codec_name}")
    if require_alpha and not out.has_alpha:
        raise VideoProcessError("视频缺少透明通道，无法作为角色片段使用", internal=f"pix_fmt={out.pix_fmt}")
    frames = max(1, round(out.duration_seconds * out.fps))
    return ClipProcessResult(
        path=dst,
        sha256=_sha256(dst),
        bytes=dst.stat().st_size,
        frames=frames,
        duration_ms=round(out.duration_seconds * 1000),
        width=out.width,
        height=out.height,
    )


def extract_cover(src: Path, dst: Path, *, at_seconds: float = 0.0) -> Path:
    """取动作首帧附近一帧作为封面（webp，保留 alpha）。封面是单帧图片，
    不做视频探针校验，只核对产物非空；封面不标记为可播放视频。"""
    args = [
        "-ss",
        f"{max(0.0, at_seconds):.3f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-an",
        "-vf",
        "format=rgba",
        str(dst),
    ]
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, label="封面")
    if not dst.exists() or dst.stat().st_size == 0:
        raise VideoProcessError("封面生成失败")
    return dst


def build_hitmask(src: Path) -> list[list[int]]:
    """按每秒采样一帧生成低分辨率 alpha 命中遮罩：返回 [sample][row][col] 的 0/1 网格。
    客户端按呈现时间取最近采样查表，经容器变换还原为屏幕坐标。"""
    probe = probe_video(src)
    samples = max(1, min(16, int(probe.duration_seconds) + 1))
    args = [
        "-i",
        str(src),
        "-vf",
        f"fps=1,scale={HITMASK_GRID_W}:{HITMASK_GRID_H},format=rgba",
        "-frames:v",
        str(samples),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-",
    ]
    from .ffmpeg import _binary, _run

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
