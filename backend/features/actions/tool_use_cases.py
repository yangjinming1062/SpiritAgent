"""动作工具业务用例：检索、受理、状态与播放，返回工具公开 JSON。"""

import json
from typing import Any

from infrastructure.database import SESSION_LOCAL
from infrastructure.text import tool_error
from pydantic import ValidationError
from sqlalchemy import select

from features.actions.design import accept_proposal
from features.actions.models import ActionProposal
from features.actions.pipeline import schedule_accepted_proposal
from features.actions.playback import request_playback
from features.actions.policy import is_expression_action
from features.actions.repository import get_action, get_active_pack, list_pack_actions
from features.actions.schemas import ActionDesignRequest, ActionPlayRequest
from features.actions.usage import action_to_dict


async def action_search_tool(user_id: int, query: str = "", limit: int = 10, **_: Any) -> str:
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
            searchable = "\n".join([stats["name"], stats["motion_description"], *map(str, stats["use_when"])]).lower()
            if needle in searchable:
                hits.append(stats)
        return json.dumps({"hits": hits[: max(1, min(limit, 20))], "total": len(hits)}, ensure_ascii=False)


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
    try:
        request = ActionDesignRequest(
            name=name,
            motion_description=motion_description,
            use_when=use_when or [],
            avoid_when=avoid_when or [],
            reason=reason or "对话中需要新的表达动作",
            duration_seconds=duration_seconds,
            clip_kind=clip_kind,
        )
    except ValidationError as exc:
        fields = sorted(
            {str(error["loc"][0]) for error in exc.errors(include_input=False, include_url=False) if error["loc"]},
        )
        return tool_error("动作设计参数不合法，请检查：" + "、".join(fields))
    async with SESSION_LOCAL() as db:
        acceptance = await accept_proposal(
            db,
            user_id,
            request,
            source="autonomous" if proactive_turn else "user_requested",
        )
        await db.commit()
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
                    select(ActionProposal).where(ActionProposal.id == proposal_id, ActionProposal.user_id == user_id),
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
                    "status": "ready" if action.status == "succeeded" and action.media_path else action.status,
                    "enabled": action.enabled,
                    "stage": action.stage,
                    "error": action.error,
                },
                ensure_ascii=False,
            )
        return tool_error("请指定 proposal_id 或 action_id")


async def action_play_tool(action_id: int, user_id: int, reason: str = "", **_: Any) -> str:
    """LLM 统一动作播放入口；对话与非对话均由此转入 request_playback。"""
    request = ActionPlayRequest(action_id=action_id, reason=reason)
    async with SESSION_LOCAL() as db:
        result = await request_playback(db, user_id, request, source="chat_expression")
        await db.commit()
    return result.model_dump_json()
