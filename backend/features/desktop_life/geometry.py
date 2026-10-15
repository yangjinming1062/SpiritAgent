"""桌景的真实显示尺寸、画幅比较和保留原图的扩图画布。"""

from io import BytesIO
from math import gcd, sqrt
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.assets.asset_store import validate_image_bytes
from infrastructure.constants import REMOTE_ASSET_DOWNLOAD_MAX_BYTES
from infrastructure.video_processing.desktop import extract_desktop_first_frame
from PIL import Image, ImageOps


def desktop_aspect_ratio(width: int, height: int) -> str:
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def desktop_aspect_matches(width: int, height: int, target_width: int, target_height: int) -> bool:
    return abs(width * target_height / (height * target_width) - 1) <= 0.01


def probe_desktop_image(data: bytes) -> tuple[int, int]:
    normalized, _mime = validate_image_bytes(data)
    with Image.open(BytesIO(normalized)) as image:
        ImageOps.exif_transpose(image, in_place=True)
        return image.size


def probe_desktop_video(data: bytes, extension: str) -> tuple[int, int]:
    # 解码首帧才能同时应用显示旋转和非方形像素；临时源不改变上传字节。
    with TemporaryDirectory(prefix="desktop-probe-") as directory:
        source = Path(directory) / f"source.{extension}"
        source.write_bytes(data)
        frame = extract_desktop_first_frame(source)
    with Image.open(BytesIO(frame)) as image:
        return image.size


def expansion_canvas(
    data: bytes,
    width: int,
    height: int,
    *,
    max_pixels: int = 2048**2,
    max_edge: int = 4096,
) -> tuple[bytes, tuple[int, int, int, int]]:
    """居中放入完整原图；只允许等比缩放，返回后续回贴的保护区域。"""
    scale = min(1.0, sqrt(max_pixels / (width * height)), max_edge / max(width, height))
    width, height = max(1, int(width * scale)), max(1, int(height * scale))
    normalized, _mime = validate_image_bytes(data)
    with Image.open(BytesIO(normalized)) as source:
        ImageOps.exif_transpose(source, in_place=True)
        with source.convert("RGB") as rgb:
            scale = min(width / rgb.width, height / rgb.height)
            size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
            left, top = (width - size[0]) // 2, (height - size[1]) // 2
            with Image.new("RGB", (width, height), (128, 128, 128)) as canvas:
                with rgb.resize(size, Image.Resampling.LANCZOS) as protected:
                    canvas.paste(protected, (left, top))
                with BytesIO() as output:
                    canvas.save(output, format="PNG")
                    return output.getvalue(), (left, top, left + size[0], top + size[1])


def native_expansion_size(generated: bytes, target_width: int, target_height: int) -> tuple[int, int]:
    """从已校验的真实产物取目标画幅内的原生像素范围，不按屏幕尺寸放大。"""
    width, height = probe_desktop_image(generated)
    # 保护区须无损保存；RGB PNG 的字节预算同时约束最大像素数。
    pixel_budget = (REMOTE_ASSET_DOWNLOAD_MAX_BYTES - 512 * 1024) // 3
    scale = min(
        1.0,
        width / target_width,
        height / target_height,
        sqrt(min(7680 * 4320, pixel_budget) / (target_width * target_height)),
        8192 / max(target_width, target_height),
    )
    return max(1, int(target_width * scale)), max(1, int(target_height * scale))


def restore_expansion_source(
    generated: bytes,
    source_canvas: bytes,
    box: tuple[int, int, int, int],
) -> bytes:
    """只采用模型产生的周边；保护区域逐像素恢复为冻结画布中的原图。"""
    with Image.open(BytesIO(source_canvas)) as frozen, Image.open(BytesIO(generated)) as output:
        ImageOps.exif_transpose(output, in_place=True)
        with (
            output.convert("RGB") as rgb,
            ImageOps.fit(
                rgb,
                frozen.size,
                method=Image.Resampling.LANCZOS,
            ) as result,
        ):
            with frozen.crop(box) as protected:
                result.paste(protected, box[:2])
            with BytesIO() as buffer:
                result.save(buffer, format="PNG")
                return buffer.getvalue()
