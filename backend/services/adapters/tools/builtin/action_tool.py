"""动作库 LLM 工具：检索、设计提案、状态查询与播放。source、授权、系统槽位与目标包由服务端绑定；工具结果不进入聊天视频自动送达链。"""

import json
from typing import Any

from components import SESSION_LOCAL, tool_error
from modules.companion import (
    ABSOLUTE_MAX_DURATION_SECONDS,
    ActionDesignRequest,
    ActionPlayRequest,
    ActionProposal,
)
from prompts.actions import (
    ACTION_DESIGN_TOOL_DESCRIPTION,
    ACTION_INSPECT_TOOL_DESCRIPTION,
    ACTION_PLAY_TOOL_DESCRIPTION,
    ACTION_SEARCH_TOOL_DESCRIPTION,
)
from sqlalchemy import select

from services.application.actions import accept_proposal, request_playback, schedule_accepted_proposal
from services.domains.actions import (
    action_to_dict,
    get_action,
    get_active_pack,
    is_expression_action,
    list_pack_actions,
)
from services.infrastructure.tool_runtime import ToolsRegistry


async def action_search_tool(
    user_id: int,
    query: str = "",
    limit: int = 10,
    **_: Any,
) -> str:
    async with SESSION_LOCAL() as db:
        pack = await get_active_pack(db, user_id)
        if pack is None:
            return tool_error("当前没有可用的形象动作")
        actions = await list_pack_actions(db, pack.id, enabled_only=True)
        needle = (query or "").strip().lower()
        hits = []
        for action in actions:
            if not is_expression_action(action):
                continue
            stats = action_to_dict(action)
            # 只匹配名称、动作描述与适用场景的文字，不含字段名、数值和布尔值；空查询匹配全部。
            searchable = "\n".join([stats["name"], stats["motion_description"], *map(str, stats["use_when"])]).lower()
            if needle in searchable:
                hits.append(stats)
        return json.dumps(
            {"pack_id": pack.id, "hits": hits[: max(1, min(limit, 20))], "total": len(hits)},
            ensure_ascii=False,
        )


async def action_design_tool(
    name: str,
    motion_description: str,
    user_id: int,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    reason: str = "",
    duration_seconds: float = 4,
    clip_kind: str = "once",
    expected_pack_id: int | None = None,
    **_: Any,
) -> str:
    try:
        request = ActionDesignRequest(
            name=name,
            motion_description=motion_description,
            use_when=use_when or [],
            avoid_when=avoid_when or [],
            reason=reason or "对话中需要新的表达动作",
            duration_seconds=duration_seconds,
            clip_kind=clip_kind,
            expected_pack_id=expected_pack_id,
        )
    except Exception as exc:  # 参数契约失败
        return tool_error(f"动作设计参数不合法：{exc}")

    async with SESSION_LOCAL() as db:
        # 对话工具入口视为用户表达驱动，计入手动额度；夜间自主提案走 nightly 的 autonomous。
        acceptance = await accept_proposal(db, user_id, request, source="user_requested")
        await db.commit()
    # 评审或重做在提案事务提交后异步执行；approve 后由流水线接手制作。
    schedule_accepted_proposal(acceptance, user_id)
    return acceptance.result.model_dump_json()


async def action_inspect_tool(
    user_id: int,
    proposal_id: int | None = None,
    action_id: int | None = None,
    **_: Any,
) -> str:
    async with SESSION_LOCAL() as db:
        if proposal_id is not None:
            proposal = (
                await db.execute(
                    select(ActionProposal).where(
                        ActionProposal.id == proposal_id,
                        ActionProposal.user_id == user_id,
                    ),
                )
            ).scalar_one_or_none()
            if proposal is None:
                return tool_error("找不到对应提案")
            return json.dumps(
                {
                    "kind": "proposal",
                    "id": proposal.id,
                    "status": proposal.status,
                    "review_decision": proposal.review_decision,
                    "review_reason": proposal.review_reason,
                    "action_id": proposal.action_id,
                },
                ensure_ascii=False,
            )
        if action_id is not None:
            action = await get_action(db, action_id)
            # 系统槽位动作不对模型开放，按不存在处理。
            if action is None or action.pack_id is None or action.system_slot:
                return tool_error("找不到对应动作")
            pack = await get_active_pack(db, user_id)
            if pack is None or action.pack_id != pack.id:
                return tool_error("动作不属于当前形象")
            return json.dumps(
                {
                    "kind": "action",
                    "id": action.id,
                    "name": action.name,
                    "status": "ready" if (action.status == "succeeded" and action.video_path) else action.status,
                    "enabled": action.enabled,
                    "stage": action.stage,
                    "error": action.error,
                },
                ensure_ascii=False,
            )
        return tool_error("请指定 proposal_id 或 action_id")


async def action_play_tool(
    action_id: int,
    user_id: int,
    reason: str = "",
    expected_pack_id: int | None = None,
    **_: Any,
) -> str:
    """LLM 统一动作播放入口；对话与非对话均由此转入 request_playback。"""
    request = ActionPlayRequest(action_id=action_id, reason=reason, expected_pack_id=expected_pack_id)
    async with SESSION_LOCAL() as db:
        result = await request_playback(db, user_id, request, source="chat_expression")
        await db.commit()
    return result.model_dump_json()


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
                    "description": "单主体运动描述：可见的姿态、节奏与神态；不含场景、镜头或产品概念。",
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
                "expected_pack_id": {
                    "type": "integer",
                    "description": "当前上下文的 expected_pack_id 或 action_search 返回的 pack_id；形象切换后刷新再填。",
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
                "expected_pack_id": {
                    "type": "integer",
                    "description": "当前上下文的 expected_pack_id 或 action_search 返回的 pack_id，用来确认仍是对当前这套形象播放。",
                },
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
