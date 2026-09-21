import json

from components import get_logger, tool_error
from prompts.generation import SELF_IMAGE_KEEP_OUTFIT, SELF_IMAGE_OUTFIT_DESCRIPTION, SELF_IMAGE_REFERENCE_TEMPLATE
from prompts.tools import IMAGE_GENERATION_DESC, IMAGE_GENERATION_PARAM_DESCS

from services.application.generation import (
    AvatarGenerationError,
    ImageGenerationError,
    generate_images,
    load_self_visual_context,
)
from services.domains.companion import render_character_identity
from services.infrastructure.llm import VisualReasoningError
from services.infrastructure.tool_runtime import REGISTRY

logger = get_logger(__name__)


async def image_generation_tool(
    prompt: str,
    size: str = "1024x1024",
    n: int = 1,
    user_id: int | None = None,
    reference_image: str | None = None,
    secondary_reference_image: str | None = None,
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
            visual = await load_self_visual_context(user_id)
            reference_image = visual.reference_image
        except (AvatarGenerationError, VisualReasoningError) as e:
            return tool_error(str(e))
        prompt = (
            SELF_IMAGE_REFERENCE_TEMPLATE.format(
                reference="图 1" if secondary_reference_image else "参考图",
                outfit=SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=visual.outfit_description)
                if visual.outfit_description
                else SELF_IMAGE_KEEP_OUTFIT,
                prompt=prompt,
            )
            + "\n"
            + render_character_identity(visual.identity)
        )
    try:
        urls = await generate_images(
            prompt,
            size=size,
            n=n,
            user_id=user_id,
            reference_image=reference_image,
            secondary_reference_image=secondary_reference_image,
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
    "description": IMAGE_GENERATION_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": IMAGE_GENERATION_PARAM_DESCS["prompt"]},
            "subject": {
                "type": "string",
                "enum": ["self"],
                "description": IMAGE_GENERATION_PARAM_DESCS["subject"],
            },
            "size": {
                "type": "string",
                "enum": IMAGE_GENERATION_SIZES,
                "description": IMAGE_GENERATION_PARAM_DESCS["size"],
            },
            "n": {"type": "integer", "description": IMAGE_GENERATION_PARAM_DESCS["n"]},
        },
        "required": ["prompt"],
    },
}


def register(registry) -> None:
    REGISTRY.register("image_generate", IMAGE_GENERATION_SCHEMA, image_generation_tool)
