"""动作库 LLM 工具：检索、设计提案、状态查询与播放。

source、授权、系统槽位与目标包由服务端绑定；工具结果不进入聊天视频自动送达链。
"""

import json
from typing import Any

from components import SESSION_LOCAL, tool_error
from modules.companion import (
    ActionDesignRequest,
    ActionPlayRequest,
    ActionProposal,
    CompanionAction,
)
from prompts.actions import (
    ACTION_DESIGN_TOOL_DESCRIPTION,
    ACTION_INSPECT_TOOL_DESCRIPTION,
    ACTION_PLAY_TOOL_DESCRIPTION,
    ACTION_SEARCH_TOOL_DESCRIPTION,
)
from sqlalchemy import select

from services.application.actions.design import accept_proposal
from services.application.actions.pipeline import schedule_action_generation, schedule_proposal_review
from services.application.actions.playback import request_playback
from services.domains.actions import (
    action_to_dict,
    get_action,
    get_active_pack,
    list_pack_actions,
)
from services.infrastructure.tool_runtime import ToolsRegistry


def _action_snapshot(action: CompanionAction) -> dict[str, Any]:
    """动作元信息快照；供检索共用。"""
    return action_to_dict(action)


async def action_search_tool(
    query: str = "",
    limit: int = 10,
    user_id: int | None = None,
    **_: Any,
) -> str:
    if user_id is None:
        return tool_error("缺少用户上下文")
    async with SESSION_LOCAL() as db:
        pack = await get_active_pack(db, user_id)
        if pack is None:
            return tool_error("当前没有可用的形象动作")
        actions = await list_pack_actions(db, pack.id, enabled_only=True)
        hits = []
        for action in actions:
            if action.status != "succeeded" or not action.video_path:
                continue
            if action.system_slot:
                continue
            stats = _action_snapshot(action)
            if query:
                text = json.dumps(stats, ensure_ascii=False).lower()
                if query.lower() not in text:
                    continue
            hits.append(stats)
        return json.dumps(
            {"pack_id": pack.id, "hits": hits[: max(1, min(limit, 20))], "total": len(hits)},
            ensure_ascii=False,
        )


async def action_design_tool(
    name: str,
    motion_description: str,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    reason: str = "",
    duration_seconds: float = 4,
    clip_kind: str = "once",
    expected_pack_id: int | None = None,
    user_id: int | None = None,
    **_: Any,
) -> str:
    if user_id is None:
        return tool_error("缺少用户上下文")
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
        result = await accept_proposal(db, user_id, request, source="user_requested")
        await db.commit()
        retry_pack_id = None
        if result.outcome == "pending_review" and result.action_id is not None and result.proposal_id is None:
            action = await get_action(db, result.action_id)
            retry_pack_id = action.pack_id if action is not None else None
    if result.outcome == "pending_review" and result.proposal_id is not None:
        # 评审异步执行（提案事务已提交）；approve 后由流水线接手制作。
        schedule_proposal_review(result.proposal_id, user_id)
    elif result.outcome == "pending_review" and result.action_id is not None and retry_pack_id is not None:
        schedule_action_generation(retry_pack_id, result.action_id, user_id)
    return result.model_dump_json()


async def action_inspect_tool(
    proposal_id: int | None = None,
    action_id: int | None = None,
    user_id: int | None = None,
    **_: Any,
) -> str:
    if user_id is None:
        return tool_error("缺少用户上下文")
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
            if action is None or action.pack_id is None:
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
    reason: str = "",
    expected_pack_id: int | None = None,
    user_id: int | None = None,
    **_: Any,
) -> str:
    """LLM 统一动作播放入口；对话与非对话均由此转入 request_playback。"""
    if user_id is None:
        return tool_error("缺少用户上下文")
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
                "name": {"type": "string", "minLength": 1, "maxLength": 64, "description": "动作显示名称。"},
                "motion_description": {
                    "type": "string",
                    "minLength": 10,
                    "maxLength": 600,
                    "description": "单主体运动描述：可见的姿态、节奏与神态；不含场景、镜头或产品概念。",
                },
                "use_when": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string"},
                    "description": "适用场景列表，如「用户主动请求拥抱」。",
                },
                "avoid_when": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string"},
                    "description": "应避免使用的场景。",
                },
                "reason": {"type": "string", "maxLength": 400, "description": "为何需要新动作；已有近义动作时先复用。"},
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 4,
                    "description": "预计时长（整秒），按动作特性填写 1–10，默认 4 秒。",
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
            name,
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
