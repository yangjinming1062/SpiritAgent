"""动作姿态图：保留原生 alpha，仅对不透明图抠像，并按交付类型准备留白。"""

from io import BytesIO
from math import ceil

from components import get_logger
from PIL import Image

from .ffmpeg import ActionMaterialRejectedError
from .matting import ForegroundMatte, require_matting_model

logger = get_logger(__name__)

ACTION_FRAME_MARGIN = 0.08


def _margin_canvas_size(size: int) -> int:
    target = ceil(size / (1 - 2 * ACTION_FRAME_MARGIN))
    while (target - size) // 2 < ceil(target * ACTION_FRAME_MARGIN):
        target += 1
    return target


def prepare_action_frame(data: bytes, *, preserve_resolution: bool = False) -> bytes:
    with Image.open(BytesIO(data)) as source:
        if getattr(source, "is_animated", False):
            raise ActionMaterialRejectedError("动作姿态图必须为单张静态图片")
        image = source.convert("RGBA")
    alpha_min, alpha_max = image.getchannel("A").getextrema()
    if alpha_max < 128:
        raise ActionMaterialRejectedError("动作姿态图缺少可见角色")
    method = "native_alpha"
    if alpha_min > 8:
        image = ForegroundMatte(require_matting_model()).apply(image)
        method = "isnet"
    alpha = image.getchannel("A")
    alpha_min, alpha_max = alpha.getextrema()
    if alpha_min > 8:
        raise ActionMaterialRejectedError("动作姿态图背景未去干净，请重新生成此动作")
    bounds = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
    if bounds is None or alpha_max < 128:
        raise ActionMaterialRejectedError("动作姿态图缺少可见角色")
    width, height = image.size
    margin_x, margin_y = ceil(width * ACTION_FRAME_MARGIN), ceil(height * ACTION_FRAME_MARGIN)
    left, top, right, bottom = bounds
    if left < margin_x or top < margin_y or right > width - margin_x or bottom > height - margin_y:
        if preserve_resolution:
            # 静态成品保留主体像素，留白不足时扩透明画布。
            target_w = _margin_canvas_size(width) if left < margin_x or right > width - margin_x else width
            target_h = _margin_canvas_size(height) if top < margin_y or bottom > height - margin_y else height
            padded = Image.new("RGBA", (target_w, target_h))
            padded.paste(image, ((target_w - width) // 2, (target_h - height) // 2))
            image = padded
        else:
            foreground = image.crop(bounds)
            foreground.thumbnail((width - 2 * margin_x, height - 2 * margin_y), Image.Resampling.LANCZOS)
            image = Image.new("RGBA", (width, height))
            image.alpha_composite(foreground, ((width - foreground.width) // 2, (height - foreground.height) // 2))
    output = BytesIO()
    image.save(output, format="PNG")
    logger.info("action frame prepared", extra={"method": method, "width": image.width, "height": image.height})
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
