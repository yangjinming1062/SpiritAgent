from prompts.tools import IMAGE_GENERATION_DESC, IMAGE_GENERATION_PARAM_DESCS, IMAGE_REGENERATE_DESC, MEDIA_INSPECT_DESC

from services.application.generation import (
    CHAT_IMAGES_PER_TURN,
    IMAGE_ASPECT_RATIOS,
    generate_chat_images,
    inspect_chat_image,
    regenerate_chat_image,
)
from services.contracts import MediaTurnState
from services.infrastructure.tool_runtime import ToolsRegistry


async def image_generation_tool(
    prompt: str,
    media_turn: MediaTurnState,
    subject: str | None = None,
    aspect_ratio: str = "1:1",
    n: int = 1,
    outfit_override: str | None = None,
    **_: object,
) -> str:
    return await generate_chat_images(
        {
            "prompt": prompt,
            "subject": subject,
            "aspect_ratio": aspect_ratio,
            "n": n,
            "outfit_override": outfit_override,
        },
        media_turn,
    )


async def media_inspect_tool(media_id: str, media_turn: MediaTurnState, **_: object) -> str:
    return await inspect_chat_image(media_id, media_turn)


async def image_regenerate_tool(
    media_id: str,
    inspection_id: str,
    correction: str,
    media_turn: MediaTurnState,
    **_: object,
) -> str:
    return await regenerate_chat_image(media_id, inspection_id, correction, media_turn)


IMAGE_GENERATION_SCHEMA = {
    "name": "image_generate",
    "description": IMAGE_GENERATION_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": IMAGE_GENERATION_PARAM_DESCS["prompt"]},
            "subject": {"type": "string", "enum": ["self"], "description": IMAGE_GENERATION_PARAM_DESCS["subject"]},
            "aspect_ratio": {
                "type": "string",
                "enum": list(IMAGE_ASPECT_RATIOS),
                "description": IMAGE_GENERATION_PARAM_DESCS["aspect_ratio"],
            },
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": CHAT_IMAGES_PER_TURN,
                "description": IMAGE_GENERATION_PARAM_DESCS["n"],
            },
            "outfit_override": {"type": "string", "description": IMAGE_GENERATION_PARAM_DESCS["outfit_override"]},
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
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


def register(registry: ToolsRegistry) -> None:
    registry.register(IMAGE_GENERATION_SCHEMA, image_generation_tool)
    registry.register(MEDIA_INSPECT_SCHEMA, media_inspect_tool)
    registry.register(IMAGE_REGENERATE_SCHEMA, image_regenerate_tool)
