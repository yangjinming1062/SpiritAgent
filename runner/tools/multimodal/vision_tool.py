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
        "Load an image into the conversation so you can see it. Accepts an image URL (http/https) or a local file path inside the SpiritAgent cache or the current working directory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "Image URL (http/https) or local file path inside SpiritAgent cache or current working directory.",
            },
        },
        "required": ["image_url"],
    },
}


def _is_path_in_safe_roots(local_path: Path) -> bool:
    """仅允许在 SpiritAgent 缓存目录 / external_skills 目录 / 进程 cwd 内打开本地图片。

    防止模型凭 ``file://`` / 绝对路径读取 ``~/.ssh/id_rsa`` 等敏感文件并把路径回声进响应。
    HTTP/HTTPS 下载路径走单独分支, 不受此约束(URL 安全闸门由 ``url_safety`` 把关)。
    """
    try:
        resolved = local_path.expanduser().resolve()
    except OSError:
        return False
    home = get_spiritagent_home().resolve()
    allowed = [home / "cache", home / "external_skills", Path(os.getcwd()).resolve()]
    for root in allowed:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


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
                    "Local image path is outside allowed roots (SpiritAgent cache, external_skills, or current working directory). "
                    "Use a URL or copy the image into the SpiritAgent cache directory first.",
                    success=False,
                )
            temp_path, should_cleanup = local_path, False
        elif await _validate_image_url_async(image_url):
            temp_path = get_spiritagent_dir("cache/vision", "temp_vision_images") / f"temp_image_{uuid.uuid4()}.jpg"
            await _download_image(image_url, temp_path)
        else:
            raise ValueError("Invalid image source. Provide an HTTP/HTTPS URL or a valid local file path.")

        def _prepare_image() -> str:
            if not (mime := _detect_image_mime_type(temp_path)):
                raise ValueError("Only real image files are supported for vision analysis.")
            return capped_image_data_url(temp_path, mime)

        img_url = await asyncio.to_thread(_prepare_image)
        size = temp_path.stat().st_size
        # 仅向模型回显文件名, 不回显绝对路径, 防避免 ``~/.ssh/id_rsa`` 等敏感路径以文件名以外形式泄露。
        safe_source = temp_path.name
        return {
            "_multimodal": True,
            "content": [
                {
                    "type": "text",
                    "text": f"Image loaded ({size:,} bytes) from {safe_source}. Inspect it and answer any pending question about it.",
                },
                {"type": "image_url", "image_url": {"url": img_url}},
            ],
            "meta": {"source": safe_source, "image_size_bytes": size},
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
