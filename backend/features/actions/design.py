"""窗口设计入口适配共享动作目录，素材制作仍由窗口模块拥有。"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from features.action_library.schemas import ActionDefinitionDesign
from features.action_library.service import accept_definition

from .materials import accepted_action_asset
from .repository import get_action, get_active_pack
from .schemas import ActionDesignRequest, ActionDesignResult


@dataclass(frozen=True)
class ProposalAcceptance:
    result: ActionDesignResult
    pack_id: int | None = None


async def accept_proposal(
    db: AsyncSession,
    user_id: int,
    request: ActionDesignRequest,
    *,
    source: str,
) -> ProposalAcceptance:
    pack = await get_active_pack(db, user_id)
    if pack is None or (request.expected_pack_id is not None and request.expected_pack_id != pack.id):
        return ProposalAcceptance(
            ActionDesignResult(outcome="rejected", message="形象已切换或没有可用动作组，请刷新后重试"),
        )
    accepted = await accept_definition(
        db,
        user_id,
        ActionDefinitionDesign(
            name=request.name,
            motion_description=request.motion_description,
            use_when=request.use_when,
            avoid_when=request.avoid_when,
            clip_kind=request.clip_kind,
            duration_seconds=int(round(request.duration_seconds)),
            reason=request.reason,
        ),
        source=source,
        origin_kind="window",
        expected_target_id=pack.id,
    )
    outcome = "rejected" if accepted.status == "rejected" else "pending_review"
    if accepted.status in {"approved", "reused"} and accepted.action_id is not None:
        action = await get_action(db, accepted.action_id)
        if action is not None and action.pack_id == pack.id and accepted_action_asset(action) is not None:
            outcome = "reused"
    return ProposalAcceptance(
        ActionDesignResult(
            outcome=outcome,
            proposal_id=accepted.definition_id or None,
            action_id=accepted.action_id,
            definition_id=accepted.definition_id or None,
            message=accepted.message,
        ),
        pack.id,
    )
