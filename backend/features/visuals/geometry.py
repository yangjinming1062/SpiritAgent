"""影像的真实显示尺寸与画幅比较。"""

from io import BytesIO
from math import ceil, gcd

from infrastructure.assets.asset_store import validate_image_bytes
from PIL import Image, ImageOps


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
