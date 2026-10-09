"""窗口生活空间的固定画幅与等比场景交付。"""

import asyncio
import io
from contextlib import nullcontext
from dataclasses import dataclass

from modules.companion import SceneImageDimensions, SceneImageSize
from PIL import Image, ImageOps

from services.infrastructure.assets import asset_store

from .character_images import image_asset_bytes
from .image_generation import ImageGenerationError

SCENE_IMAGE_SIZE = SceneImageSize(width=2560, height=1440)


@dataclass(frozen=True)
class WallpaperAsset:
    path: str
    source_size: SceneImageDimensions
    image_size: SceneImageSize


def scene_source_extension(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        return {"GIF": "gif", "JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image.format or ""]


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
    storage_directory: str,
    target: SceneImageSize,
) -> WallpaperAsset:
    data, _ = await image_asset_bytes(source_path)
    try:
        fitted, source_size = await asyncio.to_thread(_fit_wallpaper, data, target)
    except Exception as exc:
        raise ImageGenerationError("场景图片无法适配生活空间尺寸") from exc
    path = await asset_store.save_scene_wallpaper_asset_async(
        fitted,
        user_id=user_id,
        generation_id=generation_id,
        directory=storage_directory,
    )
    return WallpaperAsset(path=path, source_size=source_size, image_size=target)
