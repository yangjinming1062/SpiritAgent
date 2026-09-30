import asyncio
import contextlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from utils import get_spiritagent_dir, get_spiritagent_home, is_interrupted

from ..registry import registry, tool_error
from .helpers import (
    _detect_image_mime_type,
    _download_image,
    _validate_image_url_async,
    capped_image_data_url,
)

logger = logging.getLogger(__name__)

VISION_ANALYZE_SCHEMA = {
    "name": "vision_analyze",
    "description": (
        "Load an image into the conversation so you can see it. Accepts an http(s) image URL or a local PNG, "
        "JPEG, GIF, WebP or BMP file inside an allowed directory; a rejected path's error lists those directories."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "http(s) image URL or local image file path.",
            },
        },
        "required": ["image_url"],
    },
}


def _allowed_image_roots() -> list[Path]:
    home = get_spiritagent_home().resolve()
    return [home / "cache", home / "external_skills", Path.cwd().resolve()]


def _is_path_in_safe_roots(local_path: Path) -> bool:
    """本地图片只允许来自 SpiritAgent 缓存、external_skills 与进程工作目录，防止读取任意敏感文件。"""
    try:
        resolved = local_path.expanduser().resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(root) for root in _allowed_image_roots())


async def vision_analyze_tool(image_url: str) -> dict[str, Any] | str:
    temp_path, should_cleanup = None, True
    try:
        if is_interrupted():
            return tool_error("Interrupted", success=False)
        resolved = image_url.removeprefix("file://")
        local_path = Path(os.path.expanduser(resolved))
        if local_path.is_file():
            if not _is_path_in_safe_roots(local_path):
                return tool_error(
                    "Local image path is outside the directories this tool may read. Ask the user to provide the "
                    "image another way, such as an http(s) URL; do not copy files to work around this.",
                    success=False,
                )
            temp_path, should_cleanup = local_path, False
        elif await _validate_image_url_async(image_url):
            temp_path = get_spiritagent_dir("cache/vision") / f"temp_image_{uuid.uuid4()}.jpg"
            await _download_image(image_url, temp_path)
        else:
            raise ValueError("Invalid image source. Provide an HTTP/HTTPS URL or a valid local file path.")

        def _prepare_image() -> str:
            if not (mime := _detect_image_mime_type(temp_path)):
                raise ValueError("Only PNG, JPEG, GIF, WebP and BMP images are supported.")
            return capped_image_data_url(temp_path, mime)

        img_url = await asyncio.to_thread(_prepare_image)
        size = temp_path.stat().st_size
        # 只回显文件名。
        safe_source = temp_path.name
        text = f"Image loaded ({size:,} bytes) from {safe_source}. Inspect it and answer any pending question about it."
        return {
            "_multimodal": True,
            "content": [
                {"type": "input_text", "text": text},
                {"type": "input_image", "image_url": img_url},
            ],
            "text_summary": text,
        }
    except Exception as e:
        err_msg = f"Error loading image: {e}"
        logger.error("%s", err_msg, exc_info=True)
        return tool_error(err_msg, success=False)
    finally:
        if should_cleanup and temp_path:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(temp_path.unlink)


async def _handle_vision_analyze(args: dict[str, Any], **kw: Any) -> dict[str, Any] | str:
    return await vision_analyze_tool(args.get("image_url", ""))


registry.register_tool("vision_analyze", schema=VISION_ANALYZE_SCHEMA)(_handle_vision_analyze)
