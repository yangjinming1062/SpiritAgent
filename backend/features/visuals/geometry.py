"""影像的真实显示尺寸与画幅比较。"""

from io import BytesIO
from math import ceil, gcd

from infrastructure.assets.asset_store import validate_image_bytes
from PIL import Image, ImageOps

from .schemas import OutfitCrop


def visual_aspect_ratio(width: int, height: int) -> str:
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def visual_aspect_matches(width: int, height: int, target_width: int, target_height: int) -> bool:
    return abs(width * target_height - height * target_width) * 100 <= height * target_width


def probe_visual_image(data: bytes) -> tuple[int, int]:
    normalized, _mime = validate_image_bytes(data)
    with Image.open(BytesIO(normalized)) as image:
        ImageOps.exif_transpose(image, in_place=True)
        return image.size


def actor_reference_frame(data: bytes, target_width: int, target_height: int) -> bytes:
    """补纯色画布保留造型原像素，不重画或缩小角色；留出轻微运动空间。"""
    normalized, _mime = validate_image_bytes(data)
    with Image.open(BytesIO(normalized)) as source:
        ImageOps.exif_transpose(source, in_place=True)
        scale = max(source.width / (target_width * 0.92), source.height / (target_height * 0.92))
        size = (ceil(target_width * scale), ceil(target_height * scale))
        with Image.new("RGBA", size, (128, 128, 128, 255)) as canvas, BytesIO() as output:
            canvas.alpha_composite(
                source.convert("RGBA"), ((size[0] - source.width) // 2, (size[1] - source.height) // 2)
            )
            canvas.convert("RGB").save(output, format="PNG")
            return output.getvalue()


def cropped_outfit_frame(data: bytes, target_width: int, target_height: int, crop: OutfitCrop) -> bytes | None:
    """在造型原图中容纳选定区域，保持像素、方向和比例；不能可靠取景时交回生成。"""
    normalized, _mime = validate_image_bytes(data)
    with Image.open(BytesIO(normalized)) as source:
        ImageOps.exif_transpose(source, in_place=True)
        scale = max(
            (crop.right - crop.left) * source.width / target_width,
            (crop.bottom - crop.top) * source.height / target_height,
        )
        width, height = ceil(target_width * scale), ceil(target_height * scale)
        if min(width, height) < 128 or width > source.width or height > source.height:
            return None
        left = min(source.width - width, max(0, round((crop.left + crop.right) * source.width / 2 - width / 2)))
        top = min(source.height - height, max(0, round((crop.top + crop.bottom) * source.height / 2 - height / 2)))
        with source.crop((left, top, left + width, top + height)) as region, BytesIO() as output:
            with Image.new("RGBA", region.size, (128, 128, 128, 255)) as canvas:
                canvas.alpha_composite(region.convert("RGBA"))
                canvas.convert("RGB").save(output, format="PNG")
            return output.getvalue()


def actor_continuation_frame(data: bytes) -> bytes:
    """将实际透明帧放回视频幕布，保持原画布、位置和比例。"""
    normalized, _mime = validate_image_bytes(data)
    with Image.open(BytesIO(normalized)) as source, BytesIO() as output:
        with Image.new("RGBA", source.size, (128, 128, 128, 255)) as canvas:
            canvas.alpha_composite(source.convert("RGBA"))
            canvas.convert("RGB").save(output, format="PNG")
        return output.getvalue()
