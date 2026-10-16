"""桌景的真实显示尺寸与画幅比较。"""

from io import BytesIO
from math import gcd
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.assets.asset_store import validate_image_bytes
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
