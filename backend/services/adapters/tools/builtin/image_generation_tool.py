import json

from components import get_logger, tool_error

from services.application.generation import (
    AvatarGenerationError,
    ImageGenerationError,
    generate_images,
    resolve_self_reference_data_uri,
)
from services.infrastructure.tool_runtime import REGISTRY

logger = get_logger(__name__)


async def image_generation_tool(
    prompt: str,
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
    preferred_provider: str | list[str] | None = None,
    subject: str | None = None,
    **kwargs,
) -> str:
    """通过 image_gen 供应商链生成图片；结果按用户永久资产落盘，经鉴权资产通道加载。

    注意：本工具仅产生会话媒体卡片，禁止用于替换房间背景图（换房请使用 room_backdrop_update）。
    """
    if subject == "self":
        if user_id is None:
            return tool_error("生成自己的形象需要用户上下文")
        try:
            reference_image = await resolve_self_reference_data_uri(user_id)
        except AvatarGenerationError as e:
            return tool_error(str(e))
    try:
        urls = await generate_images(
            prompt,
            size=size,
            n=n,
            user_id=user_id,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
            preferred_provider=preferred_provider,
            persist_user_assets=True,
        )
    except ImageGenerationError as e:
        return tool_error(str(e))
    logger.info("Generated images", extra={"image_count": len(urls), "prompt": prompt, "user_id": user_id})
    return json.dumps({"success": True, "urls": urls}, ensure_ascii=False)


# MiniMax 长宽比 + 通过供应商 size→aspect_ratio 映射回传统 DALL·E 像素尺寸的兼容集合。
IMAGE_GENERATION_SIZES = [
    "1024x1024",
    "1024x1792",
    "1792x1024",
    "1:1",
    "16:9",
    "4:3",
    "3:2",
    "2:3",
    "3:4",
    "9:16",
    "21:9",
]

IMAGE_GENERATION_SCHEMA = {
    "name": "image_generate",
    "description": "Generate an image from a text description. Returns the generated image URLs.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "A detailed, descriptive prompt for the image to generate."},
            "subject": {
                "type": "string",
                "enum": ["self"],
                "description": "Set to 'self' when the image depicts YOU (the companion). The platform injects your canonical seed image as the identity reference automatically — do NOT describe your own appearance from memory; focus the prompt on scene, pose, and action.",
            },
            "size": {
                "type": "string",
                "enum": IMAGE_GENERATION_SIZES,
                "description": "Output size or aspect ratio.",
            },
            "n": {"type": "integer", "description": "Number of images to generate."},
        },
        "required": ["prompt"],
    },
}


def register(registry) -> None:
    REGISTRY.register("image_generate", IMAGE_GENERATION_SCHEMA, image_generation_tool)
