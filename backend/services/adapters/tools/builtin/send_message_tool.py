import asyncio
import json
from urllib.parse import urlparse

import httpx
from components import get_logger, is_safe_outbound, safe_outbound_async_client, tool_error
from prompts.tools import SEND_MESSAGE_DESC, SEND_MESSAGE_PARAM_DESCS

from services.domains.companion import emit_companion_message, is_still
from services.infrastructure.tool_runtime import ToolsRegistry

logger = get_logger(__name__)

WEBHOOK_TIMEOUT = 10.0


async def send_message_tool(
    message: str,
    user_id: int,
    target_webhook: str | None = None,
    **_: object,
) -> str:
    if not isinstance(message, str) or not message.strip():
        return tool_error("message must be a non-empty string")
    # 未传 webhook 时以 companion.message 投递给客户端（docs/ARCHITECTURE.md「事件持久化」）。客户端是打扰档位单一事实源，但后端在源头也做防御性拦截（非官方客户端走 /api/chat/ws 会绕过客户端侧过滤器）：静止档不写 WSEvent，不做任何主动表达。
    if not target_webhook:
        still = await is_still(user_id)
        if not still:
            await emit_companion_message(user_id, message)
        return json.dumps({"success": True, "channel": "companion", "still_suppressed": still}, ensure_ascii=False)

    parsed = urlparse(target_webhook)
    if parsed.scheme not in ("http", "https"):
        return tool_error("Invalid webhook URL scheme (must be http or https).")

    # is_safe_outbound 内部走同步 socket.getaddrinfo，必须移出事件循环
    safe, reason = await asyncio.to_thread(is_safe_outbound, parsed.hostname or "")
    if not safe:
        return tool_error(f"Refusing to POST to {parsed.hostname}: {reason}")

    # webhook 令牌常在路径或查询串里，而 httpx 异常文本带完整地址：日志与结果只保留主机、状态码或异常类型。
    host = parsed.hostname
    try:
        async with safe_outbound_async_client() as client:
            # 各 webhook 平台载荷字段不一，同时发送 text 与 content 两个常见键。
            response = await client.post(
                target_webhook,
                json={"text": message, "content": message},
                timeout=WEBHOOK_TIMEOUT,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        logger.warning("Webhook rejected message", extra={"webhook_host": host, "status": status})
        return tool_error(f"Webhook returned HTTP {status}")
    except httpx.HTTPError as e:
        logger.warning("Webhook delivery failed", extra={"webhook_host": host, "error_type": type(e).__name__})
        return tool_error(f"Webhook delivery failed ({type(e).__name__})")
    logger.info("Message sent to webhook", extra={"webhook_host": host})
    return json.dumps({"success": True, "status": response.status_code}, ensure_ascii=False)


SEND_MESSAGE_SCHEMA = {
    "name": "send_message_tool",
    "description": SEND_MESSAGE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": SEND_MESSAGE_PARAM_DESCS["message"]},
            "target_webhook": {
                "type": "string",
                "description": SEND_MESSAGE_PARAM_DESCS["target_webhook"],
            },
        },
        "required": ["message"],
    },
}


def register(registry: ToolsRegistry) -> None:
    registry.register(SEND_MESSAGE_SCHEMA, send_message_tool)
