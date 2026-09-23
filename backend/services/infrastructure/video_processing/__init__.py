"""角色视频片段处理基础设施：FFmpeg 受控封装、动作片段规范化与命中遮罩。

仅服务端使用；参数由代码构造，不执行用户字符串，不向客户端分发 FFmpeg。
"""

from .ffmpeg import VideoProbe, VideoProcessError, probe_video, run_ffmpeg
from .matting import MatteResult, matte_video, require_matting_model
from .process import (
    HITMASK_FPS,
    HITMASK_GRID_H,
    HITMASK_GRID_W,
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
from .quality import FullClipWindow, LoopWindow, select_full_clip, select_loop

__all__ = [
    "HITMASK_FPS",
    "HITMASK_GRID_H",
    "HITMASK_GRID_W",
    "MAX_CANVAS_HEIGHT",
    "MAX_CANVAS_WIDTH",
    "MAX_CLIP_SECONDS",
    "MAX_SOURCE_BYTES",
    "TARGET_EXT",
    "ClipProcessResult",
    "FullClipWindow",
    "LoopWindow",
    "MatteResult",
    "VideoProbe",
    "VideoProcessError",
    "build_hitmask",
    "extract_cover",
    "matte_video",
    "prepare_action_clip",
    "probe_video",
    "require_matting_model",
    "run_ffmpeg",
    "select_full_clip",
    "select_loop",
]
