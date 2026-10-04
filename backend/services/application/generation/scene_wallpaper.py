"""场景图片的物理尺寸与等比壁纸交付。"""

import asyncio
import io
from contextlib import nullcontext
from dataclasses import dataclass
from math import gcd

from modules.companion import SceneImageDimensions, SceneImageSize
from PIL import Image, ImageOps

from services.infrastructure.assets import asset_store

from .character_images import image_asset_bytes
from .image_generation import ImageGenerationError


@dataclass(frozen=True)
class WallpaperAsset:
    path: str
    source_size: SceneImageDimensions
    image_size: SceneImageSize


def scene_aspect_ratio(size: SceneImageSize) -> str:
    divisor = gcd(size.width, size.height)
    return f"{size.width // divisor}:{size.height // divisor}"


def scene_image_size(data: bytes) -> SceneImageDimensions:
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if image.getexif().get(274) in {5, 6, 7, 8}:
            width, height = height, width
        return SceneImageDimensions(width=width, height=height)


def _fit_wallpaper(data: bytes, target: SceneImageSize) -> tuple[bytes, SceneImageDimensions]:
    with Image.open(io.BytesIO(data)) as source:
        ImageOps.exif_transpose(source, in_place=True)
        source_size = SceneImageDimensions(width=source.width, height=source.height)
        rgb = nullcontext(source) if source.mode == "RGB" else source.convert("RGB")
        with (
            rgb as image,
            ImageOps.fit(image, (target.width, target.height), method=Image.Resampling.LANCZOS) as fitted,
            io.BytesIO() as output,
        ):
            fitted.save(output, format="PNG")
            return output.getvalue(), source_size


async def prepare_scene_wallpaper(
    user_id: int,
    source_path: str,
    *,
    generation_id: str,
    target: SceneImageSize,
) -> WallpaperAsset:
    data, _ = await image_asset_bytes(source_path)
    try:
        fitted, source_size = await asyncio.to_thread(_fit_wallpaper, data, target)
    except Exception as exc:
        raise ImageGenerationError("场景图片无法适配屏幕尺寸") from exc
    path = await asset_store.save_scene_wallpaper_asset_async(fitted, user_id=user_id, generation_id=generation_id)
    return WallpaperAsset(path=path, source_size=source_size, image_size=target)
