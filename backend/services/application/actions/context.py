"""动作上下文快照：陪伴会话每次模型调用前重建并追加到环境提示，夜间规划直接读取字段。可播放列表只含已启用且素材就绪的表达动作；不含额度数值，避免限额影响创建意图。"""

import json
from dataclasses import dataclass, field
from typing import Any

from components import resolve_prompt_text
from modules.companion import ActionProposal
from prompts.actions import ACTION_CONTEXT_GUIDANCES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import action_to_dict, get_active_pack, is_expression_action, list_pack_actions


@dataclass
class ActionContextSnapshot:
    pack_id: int | None = None
    catalog_version: int = 0
    ready_actions: list[dict[str, Any]] = field(default_factory=list)
    in_flight_proposals: list[dict[str, Any]] = field(default_factory=list)
    recent_rejections: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_block(self, *, language: str = "zh") -> str:
        """动作内容使用 JSON 保留资料边界与完整适用条件。"""
        payload = {
            "expected_pack_id": self.pack_id,
            "ready_actions_total": len(self.ready_actions),
            "ready_actions": [
                {
                    key: item.get(key)
                    for key in (
                        "action_id",
                        "name",
                        "motion_description",
                        "use_when",
                        "avoid_when",
                        "kind",
                        "duration_seconds",
                    )
                }
                for item in self.ready_actions[:12]
            ],
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
    snapshot = ActionContextSnapshot(pack_id=pack.id, catalog_version=pack.catalog_version)

    actions = await list_pack_actions(db, pack.id, enabled_only=False)
    actions_by_id = {action.id: action for action in actions}
    snapshot.ready_actions = [action_to_dict(action) for action in actions if is_expression_action(action)]

    proposals = (
        (
            await db.execute(
                select(ActionProposal)
                .where(
                    ActionProposal.user_id == user_id,
                    ActionProposal.pack_id == pack.id,
                    ActionProposal.status.in_(("pending", "deferred", "approved")),
                )
                .order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc()),
            )
        )
        .scalars()
        .all()
    )
    # 未完成提案保留真实制作状态；已成功或已删除动作不再出现在 in_flight。
    for p in proposals:
        action = actions_by_id.get(p.action_id) if p.action_id is not None else None
        if p.status == "approved" and (action is None or action.status == "succeeded"):
            continue
        snapshot.in_flight_proposals.append(
            {
                "proposal_id": p.id,
                "status": p.status,
                "action_id": p.action_id,
                "action_status": action.status if action is not None else None,
                "reason": p.review_reason or "",
                "error": action.error if action is not None else None,
                "design": json.loads(p.design_json or "{}"),
            },
        )

    rejected = (
        (
            await db.execute(
                select(ActionProposal)
                .where(
                    ActionProposal.user_id == user_id,
                    ActionProposal.pack_id == pack.id,
                    ActionProposal.status == "rejected",
                )
                # 拒绝行的 updated_at 即拒绝时刻，复用重审的旧提案按最近一次拒绝排序。
                .order_by(ActionProposal.updated_at.desc(), ActionProposal.id.desc())
                .limit(5),
            )
        )
        .scalars()
        .all()
    )
    for p in rejected:
        design = json.loads(p.design_json or "{}")
        snapshot.recent_rejections.append(
            {
                "proposal_id": p.id,
                "design": design,
                "reason": p.review_reason or "",
            },
        )

    return snapshot
