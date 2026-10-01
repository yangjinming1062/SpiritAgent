"""陪伴主对话只提交发布意图和读取状态。"""

from uuid import UUID

from components import SESSION_LOCAL
from prompts.tools import POST_PUBLISH_DESC, POST_PUBLISH_PARAM_DESCS, POST_STATUS_DESC, POST_STATUS_PARAM_DESCS

from services.application.posts import request_publication
from services.contracts import MemoryScope
from services.domains.posts import PostError, publication_status
from services.infrastructure.tool_runtime import ToolsRegistry


async def post_publish_tool(
    intent: str,
    user_id: int,
    parent_session_id: str,
    tool_call_id: str,
    memory_scope: MemoryScope | None,
    proactive_turn: bool,
    user_message: str,
    content_type: str = "auto",
    **_: object,
) -> str:
    if memory_scope is None or memory_scope.system_preset_id != "companion":
        raise PostError("动态发布只用于伙伴陪伴")
    result = await request_publication(
        user_id,
        key=f"chat:{parent_session_id}:{tool_call_id}",
        trigger="conversation",
        intent=intent,
        requested_type=content_type,
        user_message="" if proactive_turn else user_message,
        autonomous=proactive_turn,
    )
    return result.model_dump_json()


async def post_status_tool(
    publication_id: str,
    user_id: int,
    memory_scope: MemoryScope | None,
    **_: object,
) -> str:
    if memory_scope is None or memory_scope.system_preset_id != "companion":
        raise PostError("动态查询只用于伙伴陪伴")
    UUID(publication_id)
    async with SESSION_LOCAL() as db:
        result = await publication_status(db, user_id, publication_id)
    return result.model_dump_json()


def register(registry: ToolsRegistry) -> None:
    registry.register(
        {
            "name": "post_publish",
            "description": POST_PUBLISH_DESC,
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "maxLength": 1000,
                        "description": POST_PUBLISH_PARAM_DESCS["intent"],
                    },
                    "content_type": {
                        "type": "string",
                        "enum": ["auto", "text", "image", "video", "audio"],
                        "description": POST_PUBLISH_PARAM_DESCS["content_type"],
                    },
                },
                "required": ["intent"],
                "additionalProperties": False,
            },
        },
        post_publish_tool,
    )
    registry.register(
        {
            "name": "post_status",
            "description": POST_STATUS_DESC,
            "parameters": {
                "type": "object",
                "properties": {
                    "publication_id": {"type": "string", "description": POST_STATUS_PARAM_DESCS["publication_id"]},
                },
                "required": ["publication_id"],
                "additionalProperties": False,
            },
        },
        post_status_tool,
    )
