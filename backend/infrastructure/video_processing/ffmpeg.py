"""FFmpeg / ffprobe 受控进程封装：固定二进制名与受限参数。

媒体解码、编码及全片扫描不设耗时上限，仅元信息探测设置独立预算。
不执行任意脚本或用户字符串参数，输出文件落在调用方给定的 data 目录内。
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from infrastructure.logger import get_logger

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

FFPROBE_TIMEOUT_SECONDS = 600.0


class VideoProcessError(RuntimeError):
    """视频处理失败；str(exc) 为可展示的公开文案，internal 保留原始输出并作为备注随回溯进入日志。"""

    def __init__(self, message: str, *, internal: str | None = None) -> None:
        super().__init__(message)
        self.internal = internal
        if internal:
            self.add_note(internal)


class ActionMaterialRejectedError(VideoProcessError):
    """素材违反确定性机械门禁；重新处理同一素材无法恢复。"""


class VideoToolUnavailableError(VideoProcessError):
    """媒体工具缺失、无法启动或元信息探测超时；不能据此判定视频成品本身有问题。"""


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
    profile: str = ""
    format_name: str = ""
    audio_codecs: tuple[str, ...] = ()

    @property
    def has_alpha(self) -> bool:
        return self.alpha_mode == 1 or self.pix_fmt in _ALPHA_PIX_FMTS


def _binary(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise VideoToolUnavailableError(f"{name} 不可用，无法处理视频", internal=f"{name} not found on PATH")
    return resolved


def _run(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # 参数列表固定构造，不经 shell。
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoToolUnavailableError("媒体信息读取超时", internal=str(exc)) from exc
    except OSError as exc:
        raise VideoToolUnavailableError("视频处理进程启动失败", internal=str(exc)) from exc


def probe_video(path: Path) -> VideoProbe:
    """ffprobe 读取容器元信息；文件不可解码或缺少视频流时失败。"""
    args = [
        _binary("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate,codec_name,pix_fmt,profile:stream_tags=alpha_mode:format=duration,format_name",
        "-of",
        "json",
        str(path),
    ]
    proc = _run(args, timeout=FFPROBE_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        raise VideoProcessError("视频元信息读取失败", internal=proc.stderr.decode("utf-8", "replace")[:2000])
    payload = json.loads(proc.stdout.decode("utf-8", "replace"))
    streams = payload.get("streams") or []
    if not streams:
        raise VideoProcessError("视频文件中没有视频流")
    stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if stream is None:
        raise VideoProcessError("视频文件中没有视频流")
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
        profile=str(stream.get("profile") or ""),
        format_name=str(payload.get("format", {}).get("format_name") or ""),
        audio_codecs=tuple(
            str(item.get("codec_name"))
            for item in streams
            if item.get("codec_type") == "audio" and item.get("codec_name")
        ),
    )


def alpha_input_args(probe: VideoProbe) -> list[str]:
    """VP9 alpha 必须显式使用 libvpx 解码；原生 FFmpeg VP9 解码器会丢弃辅助 alpha。"""
    if probe.codec_name == "vp9" and probe.has_alpha:
        return ["-c:v", "libvpx-vp9"]
    if probe.codec_name == "vp8" and probe.has_alpha:
        return ["-c:v", "libvpx"]
    return []


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


def ffmpeg_stdout(args: list[str], *, failure_message: str) -> bytes:
    """执行输出到标准输出的 ffmpeg 并返回 stdout；非零退出码转 VideoProcessError（stderr 摘要进 internal）。"""
    proc = _run([_binary("ffmpeg"), "-v", "error", *args])
    if proc.returncode != 0:
        raise VideoProcessError(failure_message, internal=proc.stderr.decode("utf-8", "replace")[:2000])
    return proc.stdout


def video_frame_times(path: Path) -> tuple[float, ...]:
    """读取实际解码帧时间；循环采样不能用平均帧率推算 VFR 视频的尾帧。"""
    proc = _run(
        [
            _binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(path),
        ],
    )
    if proc.returncode != 0:
        raise VideoProcessError("视频帧时间读取失败", internal=proc.stderr.decode("utf-8", "replace")[:2000])
    payload = json.loads(proc.stdout)
    times = tuple(
        float(frame["best_effort_timestamp_time"])
        for frame in payload.get("frames", ())
        if frame.get("best_effort_timestamp_time") is not None
    )
    if not times:
        raise VideoProcessError("视频没有可解码画面")
    return times


def run_ffmpeg(args: list[str], *, label: str) -> None:
    """执行一次 ffmpeg；非零退出码统一转 VideoProcessError（stderr 摘要进 internal）。"""
    full = [_binary("ffmpeg"), "-v", "error", "-y", *args]
    proc = _run(full)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace")
        logger.warning("ffmpeg %s failed", label, extra={"stderr": stderr[:2000]})
        raise VideoProcessError(f"视频{label}失败", internal=stderr[:2000])
