import json

from features.companion.affect_emit import emit_companion_message
from features.companion.disturbance import is_still
from infrastructure.api_ids import ResourceId
from infrastructure.text import tool_error
from runtime.tools.prompts import SEND_MESSAGE_DESC, SEND_MESSAGE_PARAM_DESCS
from runtime.tools.registry import ToolsRegistry


async def send_message_tool(message: str, user_id: ResourceId, **_context: object) -> str:
    if not isinstance(message, str) or not message.strip():
        return tool_error("message must be a non-empty string")
    still = await is_still(user_id)
    if not still:
        await emit_companion_message(user_id, message)
    return json.dumps({"success": True, "channel": "companion", "still_suppressed": still}, ensure_ascii=False)


SEND_MESSAGE_SCHEMA = {
    "name": "send_message_tool",
    "description": SEND_MESSAGE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": SEND_MESSAGE_PARAM_DESCS["message"]},
        },
        "required": ["message"],
        "additionalProperties": False,
    },
}


def register(registry: ToolsRegistry) -> None:
    registry.register(SEND_MESSAGE_SCHEMA, send_message_tool)
