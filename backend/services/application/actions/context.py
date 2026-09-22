"""动作上下文快照（{{ACTION_CONTEXT}}）：每次模型调用前刷新。

只列可播放能力与未完成/拒绝创意；不展示制作额度，避免限额影响创建意图。
生成中、失败、停用、已删除动作不进入可播放列表。
"""

import json
from dataclasses import dataclass, field
from typing import Any

from components import resolve_prompt_text
from modules.companion.actions import ActionProposal, CompanionActionPack
from prompts.actions import ACTION_CONTEXT_GUIDANCES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions.repository import list_pack_actions
from services.domains.actions.usage import action_to_dict


@dataclass
class ActionContextSnapshot:
    pack_id: int | None = None
    catalog_version: int = 0
    character_id: int | None = None
    outfit_id: int | None = None
    ready_actions: list[dict[str, Any]] = field(default_factory=list)
    in_flight_proposals: list[dict[str, Any]] = field(default_factory=list)
    recent_rejections: list[dict[str, Any]] = field(default_factory=list)
    device_visible: bool = True

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
            "recent_rejections": self.recent_rejections[:3],
            **({"device_visible": False} if not self.device_visible else {}),
        }
        return resolve_prompt_text(ACTION_CONTEXT_GUIDANCES, language) + "\n" + json.dumps(payload, ensure_ascii=False)


async def build_action_context(
    db: AsyncSession,
    user_id: int,
    *,
    device_visible: bool = True,
) -> ActionContextSnapshot:
    pack_row = await db.execute(
        select(CompanionActionPack).where(
            CompanionActionPack.user_id == user_id,
            CompanionActionPack.active.is_(True),
        ),
    )
    pack = pack_row.scalar_one_or_none()
    snapshot = ActionContextSnapshot(device_visible=device_visible)

    if pack is None:
        return snapshot

    snapshot.pack_id = pack.id
    snapshot.catalog_version = pack.catalog_version
    snapshot.character_id = pack.character_id
    snapshot.outfit_id = pack.outfit_id

    actions = await list_pack_actions(db, pack.id, enabled_only=False)
    actions_by_id = {action.id: action for action in actions}
    for action in actions:
        if not action.enabled or action.status != "succeeded" or not action.video_path:
            continue
        # 系统产品槽位不进 LLM 表达清单，与 prompt_runtime.available_actions 同一过滤。
        if action.system_slot:
            continue
        snapshot.ready_actions.append(action_to_dict(action))

    proposals = (
        (
            await db.execute(
                select(ActionProposal).where(
                    ActionProposal.user_id == user_id,
                    ActionProposal.pack_id == pack.id,
                    ActionProposal.status.in_(("pending", "deferred", "approved")),
                ),
            )
        )
        .scalars()
        .all()
    )
    # 未完成提案保留真实制作状态；已成功或已删除动作不再出现在 in_flight。
    for p in proposals:
        action = actions_by_id.get(p.action_id)
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
                "name": json.loads(p.design_json or "{}").get("name", ""),
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
                .order_by(ActionProposal.created_at.desc())
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
                "name": design.get("name", ""),
                "reason": p.review_reason or "",
            },
        )

    return snapshot
