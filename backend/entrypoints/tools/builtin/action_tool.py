"""伙伴统一动作工具；账户与本轮呈现视图由派发层注入。"""

import json
from typing import Any

from features.action_library.schemas import ABSOLUTE_MAX_DURATION_SECONDS
from runtime.tools.registry import ToolsRegistry
from workflows.companion_action_prompts import (
    ACTION_DESIGN_MOTION_DESCRIPTION,
    ACTION_DESIGN_TOOL_DESCRIPTION,
    ACTION_INSPECT_TOOL_DESCRIPTION,
    ACTION_PLAY_TOOL_DESCRIPTION,
    ACTION_SEARCH_TOOL_DESCRIPTION,
)
from workflows.companion_actions import (
    ActionViewSnapshot,
    design_action_reference,
    inspect_action_reference,
    play_action_reference,
    search_action_reference,
)


async def action_search_tool(
    user_id: int,
    query: str = "",
    limit: int = 10,
    action_view: ActionViewSnapshot | None = None,
    **_: Any,
) -> str:
    return json.dumps(await search_action_reference(user_id, action_view, query=query, limit=limit), ensure_ascii=False)


async def action_design_tool(
    name: str,
    motion_description: str,
    user_id: int,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    reason: str = "",
    duration_seconds: int = 4,
    clip_kind: str = "once",
    proactive_turn: bool = False,
    action_view: ActionViewSnapshot | None = None,
    **_: Any,
) -> str:
    result = await design_action_reference(
        name=name,
        motion_description=motion_description,
        user_id=user_id,
        use_when=use_when,
        avoid_when=avoid_when,
        reason=reason,
        duration_seconds=duration_seconds,
        clip_kind=clip_kind,
        proactive_turn=proactive_turn,
        action_view=action_view,
    )
    return json.dumps(result, ensure_ascii=False)


async def action_inspect_tool(
    user_id: int,
    action_ref: str | None = None,
    definition_id: int | None = None,
    action_view: ActionViewSnapshot | None = None,
    **_: Any,
) -> str:
    result = await inspect_action_reference(user_id, action_view, action_ref=action_ref, definition_id=definition_id)
    return json.dumps(result, ensure_ascii=False)


async def action_play_tool(
    action_ref: str,
    user_id: int,
    reason: str = "",
    proactive_turn: bool = False,
    action_view: ActionViewSnapshot | None = None,
    **_: Any,
) -> str:
    result = await play_action_reference(
        user_id,
        action_ref,
        action_view,
        reason=reason,
        source="autonomous" if proactive_turn else "chat_expression",
    )
    return json.dumps(result, ensure_ascii=False)


def register(registry: ToolsRegistry) -> None:
    definitions = [
        (
            "action_search",
            action_search_tool,
            {
                "query": {
                    "type": "string",
                    "description": "完整关键词文本匹配；留空不筛选。返回数量仍受 limit 限制。",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            [],
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
                    "description": "适用场景列表，如「用户主动请求拥抱」。",
                },
                "avoid_when": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "maxLength": 120},
                    "description": "应避免使用的场景。",
                },
                "reason": {"type": "string", "maxLength": 400, "description": "为何需要新动作；已有近义动作时先复用。"},
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": int(ABSOLUTE_MAX_DURATION_SECONDS),
                    "default": 4,
                    "description": "预计时长（整秒），按一次可见表达与必要停留填写 1–15，默认 4 秒；长期陪伴不意味着延长单个片段，不用额外动作填满。",
                },
                "clip_kind": {
                    "type": "string",
                    "enum": ["loop", "once"],
                    "default": "once",
                    "description": "loop 持续保持状态或自然连续的运动，首尾姿态、接触及道具位置和可见状态均须相接；翻到新页等不能自然闭合的过程使用 once，或只设计可持续的阅读阶段，不靠倒放或重置闭合。once 完成一次表达并自然收束。",
                },
            },
            ["name", "motion_description"],
        ),
        (
            "action_inspect",
            action_inspect_tool,
            {
                "definition_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "action_design 返回的动作设计标识，与 action_ref 二选一。",
                },
                "action_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "当前清单中的 action_ref，与 definition_id 二选一。",
                },
            },
            [],
        ),
        (
            "action_play",
            action_play_tool,
            {
                "action_ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "来自动作清单、action_search 或 action_inspect 的当前 action_ref。",
                },
                "reason": {"type": "string", "maxLength": 200, "description": "本次表演的简短情境依据。"},
            },
            ["action_ref"],
        ),
    ]
    descriptions = {
        "action_search": ACTION_SEARCH_TOOL_DESCRIPTION,
        "action_design": ACTION_DESIGN_TOOL_DESCRIPTION,
        "action_inspect": ACTION_INSPECT_TOOL_DESCRIPTION,
        "action_play": ACTION_PLAY_TOOL_DESCRIPTION,
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
