"""角色图片透明化及静态动作图片处理。"""

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from components import REMOTE_ASSET_DOWNLOAD_MAX_BYTES, get_logger
from PIL import Image

from services.infrastructure.assets import compute_file_sha256

from .ffmpeg import ActionMaterialRejectedError, VideoProcessError
from .matting import ForegroundMatte, require_matting_model
from .process import HITMASK_ALPHA_THRESHOLD, HITMASK_GRID_H, HITMASK_GRID_W, MAX_SOURCE_BYTES
from .quality import validate_action_image, validate_transparent_image

_MAX_IMAGE_PIXELS = 3840 * 2160
logger = get_logger(__name__)


def prepare_transparent_image(data: bytes) -> bytes:
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
        validate_transparent_image(image)
    except ActionMaterialRejectedError:
        model = require_matting_model()
        try:
            image = ForegroundMatte(model).apply(image)
        except VideoProcessError:
            raise
        except Exception as exc:
            raise VideoProcessError("图片背景处理失败，请稍后重试", internal=str(exc)) from exc
        validate_transparent_image(image)
        method = "isnet"
    output = BytesIO()
    try:
        image.save(output, format="PNG")
    except OSError as exc:
        raise VideoProcessError("透明图片编码失败，请稍后重试", internal=str(exc)) from exc
    logger.info("transparent image prepared", extra={"method": method, "width": image.width, "height": image.height})
    return output.getvalue()


@dataclass(frozen=True)
class ImageProcessResult:
    sha256: str
    width: int
    height: int
    cover: bytes
    hitmask: list[int]
    content_rect: tuple[float, float, float, float]


def _load_image(src: Path) -> Image.Image:
    if src.stat().st_size > MAX_SOURCE_BYTES:
        raise ActionMaterialRejectedError("动作图片文件过大")
    try:
        with Image.open(src) as source:
            if source.format not in {"PNG", "WEBP"} or getattr(source, "is_animated", False):
                raise ActionMaterialRejectedError("动作图片须为 PNG 或静态 WebP")
            if source.width * source.height > _MAX_IMAGE_PIXELS:
                raise ActionMaterialRejectedError("动作图片分辨率超出处理上限")
            return source.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        if isinstance(exc, OSError) and exc.errno is not None:
            raise
        raise ActionMaterialRejectedError("动作图片无法解码，请提供有效图片", internal=str(exc)) from exc


def _image_hitmask(image: Image.Image) -> list[int]:
    alpha = np.asarray(image.getchannel("A").resize((HITMASK_GRID_W, HITMASK_GRID_H), Image.Resampling.BILINEAR))
    return [sum(1 << col for col in range(HITMASK_GRID_W) if row[col] >= HITMASK_ALPHA_THRESHOLD) for row in alpha]


def prepare_action_image(src: Path, dst: Path, *, canvas_w: int, canvas_h: int) -> ImageProcessResult:
    """透明 PNG/静态 WebP 独立处理为 PNG；仅扩最小同宽高比画布，不缩放主体。"""
    if type(canvas_w) is not int or type(canvas_h) is not int or canvas_w <= 0 or canvas_h <= 0:
        raise VideoProcessError("图片画布必须为正整数尺寸")
    image = _load_image(src)
    # 原图先过贴边门禁，补边不能掩盖源图已有的身体裁切。
    validate_action_image(image)
    divisor = math.gcd(canvas_w, canvas_h)
    unit_w, unit_h = canvas_w // divisor, canvas_h // divisor
    multiple = max((image.width + unit_w - 1) // unit_w, (image.height + unit_h - 1) // unit_h)
    width, height = unit_w * multiple, unit_h * multiple
    if width * height > _MAX_IMAGE_PIXELS:
        raise ActionMaterialRejectedError("动作图片画布超出处理上限")
    output = Image.new("RGBA", (width, height))
    output.paste(image, ((width - image.width) // 2, height - image.height))
    validate_action_image(output)
    bounds = output.getchannel("A").point(lambda value: 255 if value >= HITMASK_ALPHA_THRESHOLD else 0).getbbox()
    if bounds is None:
        raise ActionMaterialRejectedError("动作图片缺少可见角色")
    left, top, right, bottom = bounds
    cover = BytesIO()
    output.save(cover, format="WEBP", lossless=True)
    hitmask = _image_hitmask(output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    output.save(dst, format="PNG")
    return ImageProcessResult(
        sha256=compute_file_sha256(dst),
        width=width,
        height=height,
        cover=cover.getvalue(),
        hitmask=hitmask,
        content_rect=(left / width, top / height, right / width, bottom / height),
    )
