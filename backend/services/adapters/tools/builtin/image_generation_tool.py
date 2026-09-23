import asyncio
import json

from components import SESSION_LOCAL, get_logger, tool_error
from prompts.tools import IMAGE_GENERATION_DESC, IMAGE_GENERATION_PARAM_DESCS

from services.application.generation import (
    AvatarGenerationError,
    ImageGenerationError,
    apply_outfit_override,
    build_self_image_prompt,
    generate_character_images,
    generate_images,
    load_self_visual_context,
    optional_outfit_image_reference,
)
from services.domains.companion import character_snapshot_is_current, render_character_identity
from services.infrastructure.assets import unlink_companion_asset
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
    outfit_override: str | None = None,
    **kwargs,
) -> str:
    """通过 image_gen 供应商链生成图片；结果按用户永久资产落盘，经鉴权资产通道加载。

    本工具创作供用户查看的图片；改变伙伴所在的环境需使用 scene_list、scene_activate 或 scene_create。
    """

    if subject == "self":
        if user_id is None:
            return tool_error("生成自己的形象需要用户上下文")
        try:
            visual = await load_self_visual_context(user_id)
        except (AvatarGenerationError, VisualReasoningError) as e:
            return tool_error(str(e))
        plan = apply_outfit_override(visual, outfit_override)
        reference_image = visual.reference_image
        secondary_reference_image = await optional_outfit_image_reference(plan, user_id)
        prompt = build_self_image_prompt(plan, prompt, has_outfit_reference=bool(secondary_reference_image))
    try:
        if subject == "self":
            urls = await generate_character_images(
                prompt,
                size=size,
                n=n,
                user_id=user_id,
                reference_image=reference_image,
                secondary_reference_image=secondary_reference_image,
                identity_reference=visual.reference_image,
                identity_text=render_character_identity(visual.identity),
            )
        else:
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
    if subject == "self":
        async with SESSION_LOCAL() as db:
            if not await character_snapshot_is_current(db, user_id, visual.identity):
                for url in urls:
                    await asyncio.to_thread(unlink_companion_asset, url)
                return tool_error("生成期间角色外形已更新，本轮图片未交付，请使用新形象再生成")
    return json.dumps(
        {"success": True, "urls": urls},
        ensure_ascii=False,
    )


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
            "outfit_override": {
                "type": "string",
                "description": IMAGE_GENERATION_PARAM_DESCS["outfit_override"],
            },
        },
        "required": ["prompt"],
    },
}


def register(registry) -> None:
    REGISTRY.register("image_generate", IMAGE_GENERATION_SCHEMA, image_generation_tool)
