"""伙伴动作素材工具：查询与登记动作语义定义，不表示可播影像或自动开始制作。"""

import json
from typing import Any

from features.action_library import queries as action_queries
from features.action_library.schemas import ABSOLUTE_MAX_DURATION_SECONDS
from infrastructure.api_ids import ResourceId
from infrastructure.database import SESSION_LOCAL
from runtime.tools.registry import ToolsRegistry
from workflows.companion_action_prompts import ACTION_DESIGN_MOTION_DESCRIPTION
from workflows.visuals_companion import design_action_reference

ACTION_SEARCH_TOOL_DESCRIPTION = (
    "检索已登记的动作。返回名称、动作内容、适用条件、持续或短动作类型与时长；"
    "登记不代表图片或视频已经制作，制作另用 visual_* 工具。"
)
ACTION_INSPECT_TOOL_DESCRIPTION = "查看一个动作的完整定义与评审状态。"
ACTION_DESIGN_TOOL_DESCRIPTION = (
    "登记一个新动作（名称、动作内容、适用条件、持续或短动作类型与时长）。登记只保存定义并进入评审，"
    "不开始生成图片或视频；评审依据是角色身体能力与动作含义，具体环境与道具在每次生成时判断。"
    "已登记过相近动作时优先复用，不重复登记。"
)


async def action_search_tool(
    user_id: ResourceId,
    query: str = "",
    limit: int = 10,
    cursor: ResourceId | None = None,
    **_: Any,
) -> str:
    async with SESSION_LOCAL() as db:
        result = await action_queries.list_actions(
            db,
            user_id,
            query=query,
            limit=max(1, min(limit, 20)),
            cursor=cursor,
        )
    return json.dumps(
        {
            "actions": [item.model_dump(mode="json") for item in result.actions],
            "has_more": result.has_more,
            "next_cursor": str(result.next_cursor) if result.next_cursor is not None else None,
        },
        ensure_ascii=False,
    )


async def action_inspect_tool(
    user_id: ResourceId,
    action_id: ResourceId,
    **_: Any,
) -> str:
    async with SESSION_LOCAL() as db:
        item = await action_queries.action_detail(db, user_id, action_id)
    if item is None:
        return json.dumps({"error": "未找到该动作"}, ensure_ascii=False)
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=False)


async def action_design_tool(
    name: str,
    motion_description: str,
    user_id: ResourceId,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    allowed_triggers: list[str] | None = None,
    reason: str = "",
    duration_seconds: int = 4,
    clip_kind: str = "once",
    **_: Any,
) -> str:
    result = await design_action_reference(
        user_id,
        name=name,
        motion_description=motion_description,
        use_when=use_when,
        avoid_when=avoid_when,
        allowed_triggers=allowed_triggers,
        reason=reason,
        duration_seconds=duration_seconds,
        clip_kind=clip_kind,
    )
    return json.dumps({**result, "action_id": str(result["action_id"])}, ensure_ascii=False)


def register(registry: ToolsRegistry) -> None:
    definitions = [
        (
            "action_search",
            action_search_tool,
            {
                "cursor": {
                    "type": "string",
                    "pattern": "^[1-9][0-9]*$",
                    "description": "继续读取时使用上次返回的 next_cursor；首次查询省略。",
                },
                "query": {
                    "type": "string",
                    "description": "完整关键词文本匹配；留空不筛选。返回数量仍受 limit 限制。",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            [],
        ),
        (
            "action_inspect",
            action_inspect_tool,
            {
                "action_id": {
                    "type": "string",
                    "pattern": "^[1-9][0-9]*$",
                    "description": "动作标识，来自 action_search 或 action_design。",
                },
            },
            ["action_id"],
        ),
        (
            "action_design",
            action_design_tool,
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "用户可见的简短动作名称，使用当前对话的语言。",
                },
                "motion_description": {
                    "type": "string",
                    "minLength": 10,
                    "maxLength": 600,
                    "description": ACTION_DESIGN_MOTION_DESCRIPTION,
                },
                "use_when": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "maxLength": 120},
                    "description": "适用条件列表，如「用户主动请求拥抱」。",
                },
                "avoid_when": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "maxLength": 120},
                    "description": "应避免使用的条件。",
                },
                "allowed_triggers": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "enum": ["chat", "manual", "periodic", "user_return"]},
                    "description": "允许发起本动作的情境类别：chat、manual、periodic、user_return；默认 chat 与 manual。",
                },
                "reason": {"type": "string", "maxLength": 400, "description": "为何需要新动作；已有近义动作时先复用。"},
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": int(ABSOLUTE_MAX_DURATION_SECONDS),
                    "default": 4,
                    "description": "预计时长（整秒），按一次可见表达与必要停留填写 1–15，默认 4 秒。",
                },
                "clip_kind": {
                    "type": "string",
                    "enum": ["loop", "once"],
                    "default": "once",
                    "description": "loop 持续保持状态或自然连续的运动，首尾姿态、接触及道具位置和可见状态均须相接；"
                    "不能自然闭合的过程使用 once，完成一次表达并自然收束。",
                },
            },
            ["name", "motion_description"],
        ),
    ]
    descriptions = {
        "action_search": ACTION_SEARCH_TOOL_DESCRIPTION,
        "action_design": ACTION_DESIGN_TOOL_DESCRIPTION,
        "action_inspect": ACTION_INSPECT_TOOL_DESCRIPTION,
    }
    for name, handler, properties, required in definitions:
        registry.register(
            {
                "name": name,
                "description": descriptions[name],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler,
        )
