"""FFmpeg / ffprobe 受控进程封装：固定二进制名、受限参数、超时与工作目录约束。

安全边界（PIPELINE §视频）：不向客户端分发 FFmpeg，不执行任意脚本或用户字符串参数；
所有参数由本包代码构造，外部输入只能进入取值受校验的参数槽（路径、整数时间、分辨率）。
进程超时即杀，输出文件落在调用方给定的 data 目录内。
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from components import get_logger

logger = get_logger(__name__)

# 有透明通道的常见像素格式（ffprobe pix_fmt）
_ALPHA_PIX_FMTS: frozenset[str] = frozenset(
    {
        "yuva420p",
        "yuva422p",
        "yuva444p",
        "yuva444p10le",
        "yuva444p12le",
        "yuva444p16le",
        "gbrap",
        "gbrap10le",
        "gbrap12le",
        "gbrap16le",
        "yuva420p10le",
        "bgra",
        "rgba",
        "argb",
        "abgr",
    },
)

_FFMPEG_TIMEOUT_SECONDS = 600.0


class VideoProcessError(RuntimeError):
    """视频处理失败；str(exc) 为可展示的公开文案，internal 保留原始输出。"""

    def __init__(self, message: str, *, internal: str | None = None) -> None:
        super().__init__(message)
        self.internal = internal


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    fps: float
    duration_seconds: float
    codec_name: str
    pix_fmt: str
    # 容器的 alpha 声明；交付另验解码像素。
    alpha_mode: int = 0

    @property
    def has_alpha(self) -> bool:
        return self.alpha_mode == 1 or self.pix_fmt in _ALPHA_PIX_FMTS


def _binary(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise VideoProcessError(f"{name} 不可用，无法处理视频", internal=f"{name} not found on PATH")
    return resolved


def _run(args: list[str], *, timeout: float = _FFMPEG_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603 — 参数列表固定构造，不经 shell
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessError("视频处理超时", internal=str(exc)) from exc
    except OSError as exc:
        raise VideoProcessError("视频处理进程启动失败", internal=str(exc)) from exc


def probe_video(path: Path) -> VideoProbe:
    """ffprobe 读取容器元信息；文件不可解码或缺少视频流时失败。"""
    args = [
        _binary("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,codec_name,pix_fmt:stream_tags=alpha_mode:format=duration",
        "-of",
        "json",
        str(path),
    ]
    proc = _run(args)
    if proc.returncode != 0:
        raise VideoProcessError("视频元信息读取失败", internal=proc.stderr.decode("utf-8", "replace")[:2000])
    payload = json.loads(proc.stdout.decode("utf-8", "replace"))
    streams = payload.get("streams") or []
    if not streams:
        raise VideoProcessError("视频文件中没有视频流")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise VideoProcessError("视频分辨率无效")
    # avg_frame_rate 形如 "30000/1001"
    num, _, den = (stream.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    duration = float(payload.get("format", {}).get("duration") or 0.0)
    if fps <= 0 or duration <= 0:
        raise VideoProcessError("视频帧率或时长无效")
    return VideoProbe(
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration,
        codec_name=str(stream.get("codec_name") or ""),
        pix_fmt=str(stream.get("pix_fmt") or ""),
        alpha_mode=_alpha_mode(stream),
    )


def alpha_input_args(path: Path) -> list[str]:
    """VP9 alpha 必须显式使用 libvpx 解码；原生 FFmpeg VP9 解码器会丢弃辅助 alpha。"""
    probe = probe_video(path)
    if probe.codec_name == "vp9" and probe.has_alpha:
        return ["-c:v", "libvpx-vp9"]
    if probe.codec_name == "vp8" and probe.has_alpha:
        return ["-c:v", "libvpx"]
    return []


def probe_alpha_side_data(path: Path) -> bool:
    """检查 WebM 的 alpha 辅助数据是否存在；像素有效性由交付处理器另行验证。"""
    args = [
        _binary("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_packets",
        "-show_entries",
        "packet=side_data_types",
        "-of",
        "csv=p=0",
        str(path),
    ]
    proc = _run(args)
    if proc.returncode != 0:
        raise VideoProcessError("视频透明通道验收失败", internal=proc.stderr.decode("utf-8", "replace")[:2000])
    return "Matroska BlockAdditional" in proc.stdout.decode("utf-8", "replace")


def _alpha_mode(stream: dict) -> int:
    """WebM 容器的 alpha 声明（ALPHA_MODE=1）；VP9 透明片段以容器标签为准，
    ffprobe 的 pix_fmt 不反映 VP9 alpha 辅助层。"""
    tags = stream.get("tags") or {}
    for key, value in tags.items():
        if key.lower() == "alpha_mode":
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def run_ffmpeg(args: list[str], *, timeout: float = _FFMPEG_TIMEOUT_SECONDS, label: str = "ffmpeg") -> None:
    """执行一次 ffmpeg；非零退出码统一转 VideoProcessError（stderr 摘要进 internal）。"""
    full = [_binary("ffmpeg"), "-v", "error", "-y", *args]
    proc = _run(full, timeout=timeout)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace")
        logger.warning("ffmpeg %s failed", label, extra={"stderr": stderr[:2000]})
        raise VideoProcessError(f"视频{label}失败", internal=stderr[:2000])
