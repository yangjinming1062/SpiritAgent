"""保留角色图片原画布并生成透明背景。"""

from io import BytesIO

from PIL import Image

from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.logger import get_logger
from infrastructure.video_processing.ffmpeg import ActionMaterialRejectedError, VideoProcessError
from infrastructure.video_processing.matting import matte_image, require_matting_model
from infrastructure.video_processing.quality import validate_transparent_image

_MAX_IMAGE_PIXELS = 3840 * 2160
logger = get_logger(__name__)


def prepare_transparent_image(data: bytes, *, require_clean_edges: bool = True) -> bytes:
    """保留有效原生 alpha，其余按语义抠图；原尺寸、位置与构图保存为透明 PNG。"""
    if not data or len(data) > REMOTE_ASSET_DOWNLOAD_MAX_BYTES:
        raise ActionMaterialRejectedError("图片文件为空或超过处理上限")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF"} or getattr(source, "is_animated", False):
                raise ActionMaterialRejectedError("角色图片须为单张静态图片")
            if source.width * source.height > _MAX_IMAGE_PIXELS:
                raise ActionMaterialRejectedError("角色图片分辨率超出处理上限")
            image = source.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ActionMaterialRejectedError("角色图片无法解码，请提供有效图片", internal=str(exc)) from exc
    if image.getchannel("A").getextrema()[1] < 128:
        raise ActionMaterialRejectedError("图片缺少可见角色")
    method = "native_alpha"
    try:
        validate_transparent_image(image, require_clean_edges=require_clean_edges)
    except ActionMaterialRejectedError:
        model = require_matting_model()
        try:
            image = matte_image(image, model)
        except VideoProcessError:
            raise
        except Exception as exc:
            raise VideoProcessError("图片背景处理失败，请稍后重试", internal=str(exc)) from exc
        validate_transparent_image(image, require_clean_edges=require_clean_edges)
        method = "isnet"
    output = BytesIO()
    try:
        image.save(output, format="PNG")
    except OSError as exc:
        raise VideoProcessError("透明图片编码失败，请稍后重试", internal=str(exc)) from exc
    logger.info("transparent image prepared", extra={"method": method, "width": image.width, "height": image.height})
    return output.getvalue()
