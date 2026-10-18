"""当前形象可播素材与同着装共享设计的快照；登记和制作状态不冒充可播放能力。"""

import json
from dataclasses import dataclass, field
from typing import Any

from infrastructure.text import resolve_prompt_text, safe_json_loads
from sqlalchemy.ext.asyncio import AsyncSession

from features.action_library.queries import definitions_for_source, variant_summaries
from features.actions.policy import is_expression_action
from features.actions.prompts import ACTION_CONTEXT_GUIDANCES
from features.actions.repository import get_active_pack, list_pack_actions
from features.actions.usage import ACTION_PROMPT_KEYS, action_to_dict


@dataclass
class ActionContextSnapshot:
    pack_id: int | None = None
    catalog_version: int = 0
    # 动作素材中的着装，设计新动作以它为准。
    outfit_description: str = ""
    ready_actions: list[dict[str, Any]] = field(default_factory=list)
    in_flight_proposals: list[dict[str, Any]] = field(default_factory=list)
    recent_rejections: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_block(self, *, language: str = "zh") -> str:
        """动作内容使用 JSON 保留资料边界与完整适用条件。"""
        payload = {
            **({"action_outfit": self.outfit_description} if self.outfit_description else {}),
            "ready_actions_total": len(self.ready_actions),
            "ready_actions": [{key: item.get(key) for key in ACTION_PROMPT_KEYS} for item in self.ready_actions[:12]],
            "ready_actions_truncated": len(self.ready_actions) > 12,
            "in_flight_proposals": self.in_flight_proposals[:6],
            "in_flight_proposals_truncated": len(self.in_flight_proposals) > 6,
            "recent_rejections": self.recent_rejections[:3],
            "recent_rejections_truncated": len(self.recent_rejections) > 3,
        }
        return resolve_prompt_text(ACTION_CONTEXT_GUIDANCES, language) + "\n" + json.dumps(payload, ensure_ascii=False)


async def build_action_context(db: AsyncSession, user_id: int) -> ActionContextSnapshot:
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return ActionContextSnapshot()
    outfit = safe_json_loads(pack.outfit_snapshot or "", default={})
    snapshot = ActionContextSnapshot(
        pack_id=pack.id,
        catalog_version=pack.catalog_version,
        outfit_description=str(outfit.get("description") or "") if isinstance(outfit, dict) else "",
    )

    actions = await list_pack_actions(db, pack.id, enabled_only=False)
    actions_by_id = {action.id: action for action in actions}
    snapshot.ready_actions = [action_to_dict(action) for action in actions if is_expression_action(action)]

    if not pack.source_visual_key:
        return snapshot
    definitions = await definitions_for_source(db, user_id, pack.outfit_id, pack.source_visual_key)
    definitions_by_id = {definition.id: definition for definition in definitions}
    for definition in definitions:
        design = definition.model_dump(
            include={"name", "motion_description", "use_when", "avoid_when", "clip_kind", "duration_seconds"},
        )
        if definition.status == "rejected":
            snapshot.recent_rejections.append(
                {
                    "proposal_id": definition.id,
                    "definition_id": definition.id,
                    "design": design,
                    "review_reason": definition.review_reason,
                },
            )
            continue
        if definition.status not in {"pending", "reviewing", "deferred", "approved", "reused"}:
            continue
        canonical = definitions_by_id.get(definition.reuse_definition_id or definition.id)
        variants = await variant_summaries(db, user_id, canonical.id) if canonical is not None else []
        local = next(
            (variant for variant in variants if variant.target_kind == "window" and variant.target_id == pack.id),
            None,
        )
        action = actions_by_id.get(local.action_id) if local is not None and local.action_id is not None else None
        if local is not None and local.has_ready_asset and local.status not in {"disabled", "excluded", "blocked"}:
            continue
        snapshot.in_flight_proposals.append(
            {
                "proposal_id": definition.id,
                "definition_id": definition.id,
                "status": definition.status,
                "action_id": action.id if action is not None else None,
                "action_status": local.status if local is not None else None,
                "review_reason": definition.review_reason,
                "error": action.error if action is not None else None,
                "design": design,
                "sync_targets": [variant.model_dump() for variant in variants],
            },
        )

    return snapshot
