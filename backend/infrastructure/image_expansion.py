"""扩图参考画布、保护区回贴及已知占位边检查。"""

from io import BytesIO
from math import sqrt

import numpy as np
from PIL import Image, ImageOps

from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.media_geometry import centered_crop_box


def expansion_canvas(
    data: bytes,
    width: int,
    height: int,
    *,
    max_pixels: int = 2048**2,
    max_edge: int = 4096,
) -> tuple[bytes, tuple[int, int, int, int]]:
    scale = min(1.0, sqrt(max_pixels / (width * height)), max_edge / max(width, height))
    width, height = max(1, round(width * scale)), max(1, round(height * scale))
    with Image.open(BytesIO(data)) as source:
        ImageOps.exif_transpose(source, in_place=True)
        with source.convert("RGB") as rgb, Image.new("RGB", (width, height), (128, 128, 128)) as canvas:
            scale = min(width / rgb.width, height / rgb.height)
            size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
            left, top = (width - size[0]) // 2, (height - size[1]) // 2
            with rgb.resize(size, Image.Resampling.LANCZOS) as protected:
                canvas.paste(protected, (left, top))
            with BytesIO() as output:
                canvas.save(output, format="PNG")
                return output.getvalue(), (left, top, left + size[0], top + size[1])


def _has_placeholder_border(image: Image.Image, protected_box: tuple[int, int, int, int]) -> bool:
    """检查整个已知扩图区的连续灰带，允许边角或最外缘已被局部绘制。"""
    pixels = np.asarray(image, dtype=np.int16)
    height, width = pixels.shape[:2]
    left_limit, top_limit, right_limit, bottom_limit = protected_box
    if not (0 <= left_limit < right_limit <= width and 0 <= top_limit < bottom_limit <= height):
        raise ValueError("扩图保护区域无效")
    gray = (pixels.max(axis=2) - pixels.min(axis=2) <= 6) & (np.abs(pixels.mean(axis=2) - 128) <= 18)
    rows, columns = gray.mean(axis=1), gray.mean(axis=0)
    for coverage in (rows[:top_limit], rows[bottom_limit:], columns[:left_limit], columns[right_limit:]):
        broad_gray = coverage >= 0.90
        # 单像素压缩/重采样接缝不视作残留边带。
        if np.any(broad_gray[:-1] & broad_gray[1:]):
            return True
    return False


def finish_expansion(
    generated: bytes,
    source_canvas: bytes,
    original_source: bytes,
    protected_box: tuple[int, int, int, int],
    target_width: int,
    target_height: int,
) -> bytes:
    """按冻结参考恢复原图区域，再裁到交付画幅；占位边不得进入成品。"""
    with (
        Image.open(BytesIO(source_canvas)) as frozen,
        Image.open(BytesIO(original_source)) as original,
        Image.open(BytesIO(generated)) as output,
    ):
        ImageOps.exif_transpose(original, in_place=True)
        ImageOps.exif_transpose(output, in_place=True)
        pixel_budget = (REMOTE_ASSET_DOWNLOAD_MAX_BYTES - 512 * 1024) // 3
        native_width = min(output.width, output.height * frozen.width / frozen.height)
        native_height = min(output.height, output.width * frozen.height / frozen.width)
        scale = min(1.0, sqrt(pixel_budget / (native_width * native_height)), 8192 / max(native_width, native_height))
        width, height = max(1, round(native_width * scale)), max(1, round(native_height * scale))
        # 输出允许原生像素取整；参考构图及保护区始终采用同一坐标变换。
        box = (
            round(protected_box[0] * width / frozen.width),
            round(protected_box[1] * height / frozen.height),
            round(protected_box[2] * width / frozen.width),
            round(protected_box[3] * height / frozen.height),
        )
        with (
            output.convert("RGB") as rgb,
            ImageOps.fit(rgb, (width, height), method=Image.Resampling.LANCZOS) as result,
            original.convert("RGB") as source,
        ):
            if _has_placeholder_border(result, box):
                raise ValueError("扩图周边仍有灰色占位边，请重新调整图片")
            # 参考画布只供模型定位；回贴直接读取冻结原图，避免参考缩小时丢失成品细节。
            with source.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS) as protected:
                result.paste(protected, box[:2])
            crop = centered_crop_box(width, height, target_width, target_height)
            with result.crop(crop) as fitted, BytesIO() as buffer:
                fitted.save(buffer, format="PNG")
                return buffer.getvalue()
