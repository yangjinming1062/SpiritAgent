"""影像的真实显示尺寸与画幅比较。"""

from io import BytesIO
from math import gcd

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
