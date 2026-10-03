import json

from components import tool_error
from prompts.tools import SEND_MESSAGE_DESC, SEND_MESSAGE_PARAM_DESCS

from services.domains.companion import emit_companion_message, is_still
from services.infrastructure.tool_runtime import ToolsRegistry


async def send_message_tool(
    message: str,
    user_id: int,
    **context: object,
) -> str:
    if not isinstance(message, str) or not message.strip():
        return tool_error("message must be a non-empty string")
    # 旧历史中的外部地址不能改变消息目的地，也不能被静默转换成给主人发送。
    if "target_webhook" in context:
        return tool_error("External message delivery is unavailable. This tool can only message the account owner.")
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
