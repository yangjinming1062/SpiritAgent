"""动作媒体处理：静态透明图片、FFmpeg 受控视频规范化与命中遮罩。仅服务端使用；不执行用户字符串，不向客户端分发 FFmpeg。"""

from .ffmpeg import ActionMaterialRejectedError, VideoProcessError, VideoToolUnavailableError, probe_video
from .frames import ACTION_FRAME_MARGIN, action_frame_video_input, prepare_action_frame
from .image import ImageProcessResult, prepare_action_image, prepare_transparent_image
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
from .quality import validate_action_clip, validate_action_image, validate_transparent_image

__all__ = [
    "ACTION_FRAME_MARGIN",
    "ActionMaterialRejectedError",
    "HITMASK_FPS",
    "HITMASK_GRID_H",
    "HITMASK_GRID_W",
    "ImageProcessResult",
    "MAX_CANVAS_HEIGHT",
    "MAX_CANVAS_WIDTH",
    "MAX_SOURCE_BYTES",
    "TARGET_EXT",
    "VideoProcessError",
    "VideoToolUnavailableError",
    "action_frame_video_input",
    "build_hitmask",
    "extract_cover",
    "matte_video",
    "prepare_action_clip",
    "prepare_action_frame",
    "prepare_action_image",
    "prepare_transparent_image",
    "probe_video",
    "require_matting_model",
    "sample_key_frames",
    "validate_action_clip",
    "validate_action_image",
    "validate_transparent_image",
]
