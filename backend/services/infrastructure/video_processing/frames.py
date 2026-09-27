"""动作关键帧：保留原生 alpha，仅对不透明图抠像，并在视频提交前统一留白。"""

from io import BytesIO
from math import ceil

from components import get_logger
from PIL import Image

from .ffmpeg import VideoProcessError
from .matting import ForegroundMatte, require_matting_model

logger = get_logger(__name__)

ACTION_FRAME_MARGIN = 0.08


def prepare_action_frame(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGBA")
    alpha_min, alpha_max = image.getchannel("A").getextrema()
    if alpha_max < 128:
        raise VideoProcessError("动作姿态图缺少可见角色")
    method = "native_alpha"
    if alpha_min > 8:
        image = ForegroundMatte(require_matting_model()).apply(image)
        method = "isnet"
    alpha = image.getchannel("A")
    alpha_min, alpha_max = alpha.getextrema()
    if alpha_min > 8:
        raise VideoProcessError("动作姿态图背景未去干净，请重新生成此动作")
    bounds = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
    if bounds is None or alpha_max < 128:
        raise VideoProcessError("动作姿态图缺少可见角色")
    width, height = image.size
    margin_x, margin_y = ceil(width * ACTION_FRAME_MARGIN), ceil(height * ACTION_FRAME_MARGIN)
    left, top, right, bottom = bounds
    if left < margin_x or top < margin_y or right > width - margin_x or bottom > height - margin_y:
        foreground = image.crop(bounds)
        foreground.thumbnail((width - 2 * margin_x, height - 2 * margin_y), Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (width, height))
        image.alpha_composite(foreground, ((width - foreground.width) // 2, (height - foreground.height) // 2))
    output = BytesIO()
    image.save(output, format="PNG")
    logger.info("action frame prepared", extra={"method": method, "width": width, "height": height})
    return output.getvalue()


def action_frame_video_input(data: bytes) -> bytes:
    """当前视频接口不承诺保留输入 alpha；显式合成白底，透明原图仍独立保存。"""
    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGBA")
    background = Image.new("RGBA", image.size, "white")
    background.alpha_composite(image)
    output = BytesIO()
    background.convert("RGB").save(output, format="PNG")
    return output.getvalue()
