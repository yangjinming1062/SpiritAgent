"""动作库 LLM 工具：检索、设计提案、状态查询与播放。source、授权、系统槽位与目标包由服务端绑定；工具结果不进入聊天视频自动送达链。"""

from typing import Any

from features.actions import tool_use_cases as _actions
from features.actions.prompts import (
    ACTION_DESIGN_TOOL_DESCRIPTION,
    ACTION_INSPECT_TOOL_DESCRIPTION,
    ACTION_PLAY_TOOL_DESCRIPTION,
    ACTION_SEARCH_TOOL_DESCRIPTION,
)
from features.actions.schemas import ABSOLUTE_MAX_DURATION_SECONDS
from runtime.tools.registry import ToolsRegistry


async def action_search_tool(
    user_id: int,
    query: str = "",
    limit: int = 10,
    **_: Any,
) -> str:
    return await _actions.action_search_tool(user_id=user_id, query=query, limit=limit, **_)


async def action_design_tool(
    name: str,
    motion_description: str,
    user_id: int,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    reason: str = "",
    duration_seconds: float = 4,
    clip_kind: str = "once",
    proactive_turn: bool = False,
    **_: Any,
) -> str:
    return await _actions.action_design_tool(
        name=name,
        motion_description=motion_description,
        user_id=user_id,
        use_when=use_when,
        avoid_when=avoid_when,
        reason=reason,
        duration_seconds=duration_seconds,
        clip_kind=clip_kind,
        proactive_turn=proactive_turn,
        **_,
    )


async def action_inspect_tool(
    user_id: int,
    proposal_id: int | None = None,
    action_id: int | None = None,
    **_: Any,
) -> str:
    return await _actions.action_inspect_tool(user_id=user_id, proposal_id=proposal_id, action_id=action_id, **_)


async def action_play_tool(
    action_id: int,
    user_id: int,
    reason: str = "",
    **_: Any,
) -> str:
    """LLM 统一动作播放入口；对话与非对话均由此转入 request_playback。"""
    return await _actions.action_play_tool(action_id=action_id, user_id=user_id, reason=reason, **_)


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
                    "description": "角色独立完成一个表达目标所需的可见姿态、运动过程、节奏与神态。",
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
                    "description": "预计时长（整秒），按动作特性填写 1–15，默认 4 秒。",
                },
                "clip_kind": {
                    "type": "string",
                    "enum": ["loop", "once"],
                    "default": "once",
                    "description": "loop 首尾可循环；once 完整播放一次。",
                },
            },
            ["name", "motion_description"],
        ),
        (
            "action_inspect",
            action_inspect_tool,
            {
                "proposal_id": {"type": "integer", "minimum": 1, "description": "action_design 返回的提案 ID。"},
                "action_id": {"type": "integer", "minimum": 1, "description": "动作 ID。"},
            },
            [],
        ),
        (
            "action_play",
            action_play_tool,
            {
                "action_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "当前形象中的 action_id（来自动作列表、action_search 或 action_inspect），不是 proposal_id。",
                },
                "reason": {"type": "string", "maxLength": 200, "description": "本次表演的简短情境依据。"},
            },
            ["action_id"],
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
