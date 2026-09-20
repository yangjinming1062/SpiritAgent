"""角色视频片段处理基础设施：FFmpeg 受控封装、动作片段规范化与命中遮罩。

仅服务端使用；参数由代码构造，不执行用户字符串，不向客户端分发 FFmpeg。
"""

from .ffmpeg import VideoProbe, VideoProcessError, probe_video, run_ffmpeg
from .process import (
    MAX_CANVAS_HEIGHT,
    MAX_CANVAS_WIDTH,
    MAX_CLIP_SECONDS,
    MAX_SOURCE_BYTES,
    TARGET_EXT,
    ClipProcessResult,
    build_hitmask,
    extract_cover,
    prepare_action_clip,
)

__all__ = [
    "MAX_CANVAS_HEIGHT",
    "MAX_CANVAS_WIDTH",
    "MAX_CLIP_SECONDS",
    "MAX_SOURCE_BYTES",
    "TARGET_EXT",
    "ClipProcessResult",
    "VideoProbe",
    "VideoProcessError",
    "build_hitmask",
    "extract_cover",
    "prepare_action_clip",
    "probe_video",
    "run_ffmpeg",
]
