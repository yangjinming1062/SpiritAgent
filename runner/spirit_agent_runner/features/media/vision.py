import contextlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.contracts.interrupt import is_interrupted
from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.infrastructure.async_bridge import run_in_thread
from spirit_agent_runner.infrastructure.constants import get_spiritagent_dir, get_spiritagent_home
from spirit_agent_runner.infrastructure.images import (
    capped_image_data_url,
    detect_image_mime_type,
    download_image,
    validate_image_url_async,
)

logger = logging.getLogger(__name__)

VISION_ANALYZE_SCHEMA = {
    "name": "vision_analyze",
    "description": (
        "Inspect one image from an HTTP/HTTPS URL or a local PNG, JPEG, GIF, WebP or BMP file. Local paths must "
        "already be under cache, external_skills or the current working directory; outside paths are rejected. "
        "Use an allowed path or URL instead of copying files."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
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


async def vision_analyze_tool(image_url: str) -> dict[str, Any]:
    temp_path, should_cleanup = None, True
    try:
        if is_interrupted():
            return {"error": "Interrupted", "success": False}
        resolved = image_url.removeprefix("file://")
        local_path = Path(os.path.expanduser(resolved))
        if local_path.is_file():
            if not _is_path_in_safe_roots(local_path):
                return {
                    "error": "Local image path is outside the directories this tool may read. Ask the user to provide the "
                    "image another way, such as an http(s) URL; do not copy files to work around this.",
                    "success": False,
                }
            temp_path, should_cleanup = local_path, False
        elif await validate_image_url_async(image_url, config=load_config()):
            temp_path = get_spiritagent_dir("cache/vision") / f"temp_image_{uuid.uuid4()}.jpg"
            await download_image(image_url, temp_path, config=load_config())
        else:
            raise ValueError("Invalid image source. Provide an HTTP/HTTPS URL or a valid local file path.")

        def _prepare_image() -> str:
            if not (mime := detect_image_mime_type(temp_path)):
                raise ValueError("Only PNG, JPEG, GIF, WebP and BMP images are supported.")
            return capped_image_data_url(temp_path, mime)

        img_url = await run_in_thread(_prepare_image)
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
        return {"error": str(err_msg), "success": False}
    finally:
        if should_cleanup and temp_path:
            with contextlib.suppress(Exception):
                await run_in_thread(temp_path.unlink)


async def _handle_vision_analyze(args: dict[str, Any], **kw: Any) -> dict[str, Any] | str:
    return await vision_analyze_tool(args.get("image_url", ""))


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="vision_analyze",
            toolset_id="media_analysis",
            schema=VISION_ANALYZE_SCHEMA,
            handler=lambda context, args: _handle_vision_analyze(
                args,
                task_id=context.task_id,
                cancel_token=context.cancel_event,
            ),
            check_fn=None,
            is_async=True,
        ),
    ]
