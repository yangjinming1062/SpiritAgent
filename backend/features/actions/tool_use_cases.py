"""动作工具业务用例：检索、受理、状态与播放，返回工具公开 JSON。"""

import json
from typing import Any

from infrastructure.database import SESSION_LOCAL
from infrastructure.text import tool_error
from pydantic import ValidationError

from features.action_library.queries import definition_snapshot, targets_for_definition, variant_summaries
from features.actions.design import accept_proposal
from features.actions.materials import accepted_action_asset
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
            proposal = await definition_snapshot(db, user_id, proposal_id)
            if proposal is None:
                return tool_error("找不到对应提案")
            pack = await get_active_pack(db, user_id)
            if (
                pack is None
                or not pack.source_visual_key
                or proposal.outfit_id != pack.outfit_id
                or proposal.source_visual_key != pack.source_visual_key
            ):
                return tool_error("动作不属于当前形象")
            canonical = (
                await definition_snapshot(db, user_id, proposal.reuse_definition_id)
                if proposal.reuse_definition_id is not None
                else proposal
            )
            if (
                canonical is None
                or canonical.outfit_id != pack.outfit_id
                or canonical.source_visual_key != pack.source_visual_key
            ):
                return tool_error("动作资料已变化，请刷新后重试")
            targets = await targets_for_definition(db, user_id, canonical.id)
            target = next(
                (item for item in targets if item.target_kind == "window" and item.target_id == pack.id),
                None,
            )
            action = (
                await get_action(db, target.action_id)
                if target is not None and target.state != "excluded" and target.action_id is not None
                else None
            )
            if action is not None and (action.user_id != user_id or action.pack_id != pack.id or action.system_slot):
                return tool_error("动作不属于当前形象")
            variants = await variant_summaries(db, user_id, canonical.id)
            local = next(
                (item for item in variants if item.target_kind == "window" and item.target_id == pack.id),
                None,
            )
            return json.dumps(
                {
                    "kind": "proposal",
                    "id": proposal.id,
                    "definition_id": proposal.id,
                    "status": proposal.status,
                    "review_reason": proposal.review_reason,
                    "action_id": action.id if action is not None else None,
                    "action_status": local.status if local is not None else None,
                    "sync_targets": [item.model_dump() for item in variants],
                },
                ensure_ascii=False,
            )
        if action_id is not None:
            action = await get_action(db, action_id)
            if action is None or action.user_id != user_id or action.system_slot:
                return tool_error("找不到对应动作")
            pack = await get_active_pack(db, user_id)
            if pack is None or action.pack_id != pack.id:
                return tool_error("动作不属于当前形象")
            return json.dumps(
                {
                    "kind": "action",
                    "id": action.id,
                    "name": action.name,
                    "status": "ready" if accepted_action_asset(action) is not None else action.status,
                    "production_status": action.status,
                    "playable": is_expression_action(action),
                    "definition_id": action.definition_id,
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
