"""角色视频片段处理基础设施：FFmpeg 受控封装、动作片段规范化与命中遮罩。仅服务端使用；参数由代码构造，不执行用户字符串，不向客户端分发 FFmpeg。"""

from .ffmpeg import VideoProcessError, probe_video
from .frames import ACTION_FRAME_MARGIN, action_frame_video_input, prepare_action_frame
from .matting import matte_video, require_matting_model
from .process import (
    HITMASK_FPS,
    HITMASK_GRID_H,
    HITMASK_GRID_W,
    MAX_CANVAS_HEIGHT,
    MAX_CANVAS_WIDTH,
    MAX_SOURCE_BYTES,
    TARGET_EXT,
    build_hitmask,
    extract_cover,
    prepare_action_clip,
    sample_key_frames,
)
from .quality import validate_action_clip

__all__ = [
    "ACTION_FRAME_MARGIN",
    "HITMASK_FPS",
    "HITMASK_GRID_H",
    "HITMASK_GRID_W",
    "MAX_CANVAS_HEIGHT",
    "MAX_CANVAS_WIDTH",
    "MAX_SOURCE_BYTES",
    "TARGET_EXT",
    "VideoProcessError",
    "action_frame_video_input",
    "build_hitmask",
    "extract_cover",
    "matte_video",
    "prepare_action_clip",
    "prepare_action_frame",
    "probe_video",
    "require_matting_model",
    "sample_key_frames",
    "validate_action_clip",
]
