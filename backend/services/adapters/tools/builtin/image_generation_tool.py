from components import tool_error
from prompts.tools import IMAGE_GENERATION_DESC, IMAGE_REGENERATE_DESC, MEDIA_INSPECT_DESC

from services.application.generation import (
    ImageBatch,
    generate_chat_images,
    inspect_chat_image,
    regenerate_chat_image,
)
from services.contracts import MediaTurnState


async def image_generation_tool(requests: list[dict], media_turn: MediaTurnState | None = None, **kwargs) -> str:
    if media_turn is None:
        return tool_error("图片生成需要会话上下文")
    return await generate_chat_images(requests, media_turn)


async def media_inspect_tool(media_id: str, media_turn: MediaTurnState | None = None, **kwargs) -> str:
    if media_turn is None:
        return tool_error("验图需要会话上下文")
    return await inspect_chat_image(media_id, media_turn)


async def image_regenerate_tool(
    media_id: str,
    inspection_id: str,
    correction: str,
    media_turn: MediaTurnState | None = None,
    **kwargs,
) -> str:
    if media_turn is None:
        return tool_error("重做需要会话上下文")
    return await regenerate_chat_image(media_id, inspection_id, correction, media_turn)


IMAGE_GENERATION_SCHEMA = {
    "name": "image_generate",
    "description": IMAGE_GENERATION_DESC,
    "parameters": ImageBatch.model_json_schema(),
}
MEDIA_INSPECT_SCHEMA = {
    "name": "media_inspect",
    "description": MEDIA_INSPECT_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "media_id": {"type": "string", "description": "media_id returned by a media tool in this conversation."},
        },
        "required": ["media_id"],
        "additionalProperties": False,
    },
}
IMAGE_REGENERATE_SCHEMA = {
    "name": "image_regenerate",
    "description": IMAGE_REGENERATE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "media_id": {"type": "string", "description": "The inspected current image version."},
            "inspection_id": {"type": "string", "description": "The actual media_inspect result identifying defects."},
            "correction": {
                "type": "string",
                "description": "Specific corrections to the reported defects, preserving the user's original request.",
            },
        },
        "required": ["media_id", "inspection_id", "correction"],
        "additionalProperties": False,
    },
}


def register(registry) -> None:
    registry.register("image_generate", IMAGE_GENERATION_SCHEMA, image_generation_tool)
    registry.register("media_inspect", MEDIA_INSPECT_SCHEMA, media_inspect_tool)
    registry.register("image_regenerate", IMAGE_REGENERATE_SCHEMA, image_regenerate_tool)
