"""提案受理与语义去重：reused / pending_review / rejected。

受理在用户级锁内完成「门禁 → 去重 → 落库」，防止并发绕过约束。
幂等键限定 (user, source, pack, fingerprint)：换包或抑制期满后同创意可重提。
"""

import hashlib
import json
import re
import unicodedata

from modules.companion.actions import ActionProposal
from modules.companion.schemas_actions import ActionDesignRequest, ActionDesignResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions.policy import (
    ActionPolicyError,
    check_can_accept,
    get_action_accept_lock,
)
from services.domains.actions.repository import (
    get_action_by_key,
    get_active_pack,
    make_semantic_fingerprint,
)

# 动作 key：小写 ASCII slug；非 ASCII（如中文名）按语义指纹派生，保证
# 「拥抱」「打哈欠」「跳舞」映射到不同稳定 key。
_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")


def action_key_from_name(name: str, fingerprint: str) -> str:
    normalized = unicodedata.normalize("NFKC", name.strip().lower())
    slug = _KEY_STRIP_RE.sub("_", normalized).strip("_")
    if slug and any(ch.isascii() and ch.isalnum() for ch in slug.replace("_", "")):
        return slug[:32]
    return f"action_{fingerprint[:12]}"


async def accept_proposal(
    db: AsyncSession,
    user_id: int,
    request: ActionDesignRequest,
    *,
    source: str = "autonomous",
    source_message_ids: list[int] | None = None,
    idempotency_key: str = "",
) -> ActionDesignResult:
    """受理提案；不阻塞等待评审与视频。"""
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return ActionDesignResult(outcome="rejected", message="当前没有可用的形象动作")

    if request.expected_pack_id is not None and request.expected_pack_id != pack.id:
        return ActionDesignResult(outcome="rejected", message="形象已切换，请刷新动作列表")

    fingerprint = make_semantic_fingerprint(request.name, request.motion_description)
    key = action_key_from_name(request.name, fingerprint)

    async with get_action_accept_lock(user_id):
        existing = await get_action_by_key(db, pack.id, key)
        if existing and existing.status == "succeeded" and existing.video_path:
            return ActionDesignResult(
                outcome="reused",
                action_id=existing.id,
                message=f"已有相似动作「{existing.name}」，直接使用即可",
            )
        if existing and existing.status in ("queued", "running", "result_unknown"):
            return ActionDesignResult(
                outcome="pending_review",
                action_id=existing.id,
                message=f"相似动作「{existing.name}」已在制作中",
            )
        if existing and existing.status in ("failed", "cancelled"):
            # 制作失败的同名动作：保留动作身份重做素材，不新建提案。
            existing.status = "queued"
            existing.stage = "design"
            existing.error = None
            await db.flush()
            return ActionDesignResult(
                outcome="pending_review",
                action_id=existing.id,
                message=f"将重新制作动作「{existing.name}」",
            )

        pending = (
            (
                await db.execute(
                    select(ActionProposal).where(
                        ActionProposal.user_id == user_id,
                        ActionProposal.pack_id == pack.id,
                        ActionProposal.semantic_fingerprint == fingerprint,
                        ActionProposal.status == "pending",
                    ),
                )
            )
            .scalars()
            .first()
        )
        if pending is not None:
            return ActionDesignResult(
                outcome="pending_review",
                proposal_id=pending.id,
                message="已有相同创意的提案在评审中",
            )

        # deferred / 拒绝抑制期满 / 制作失败后的原创意重提：复用原提案行再评审，不撞幂等唯一约束。
        prior = (
            (
                await db.execute(
                    select(ActionProposal)
                    .where(
                        ActionProposal.user_id == user_id,
                        ActionProposal.pack_id == pack.id,
                        ActionProposal.semantic_fingerprint == fingerprint,
                        ActionProposal.status.in_(("deferred", "rejected", "approved")),
                    )
                    .order_by(ActionProposal.id.desc()),
                )
            )
            .scalars()
            .first()
        )
        if prior is not None and prior.status == "approved" and prior.action_id is not None:
            # 制作失败的已批提案：重置动作任务续跑，不重复占用制作额度。
            failed_action = await get_action_by_key(db, pack.id, key)
            if failed_action is not None and failed_action.status in ("failed", "cancelled", "result_unknown"):
                failed_action.status = "queued"
                failed_action.stage = "design"
                failed_action.error = None
                await db.flush()
                return ActionDesignResult(
                    outcome="pending_review",
                    action_id=failed_action.id,
                    message="将重新制作该动作",
                )
        if prior is not None and prior.status in ("deferred", "rejected"):
            try:
                await check_can_accept(
                    db,
                    user_id,
                    source=source,
                    duration_seconds=request.duration_seconds,
                    pack_id=pack.id,
                    semantic_fingerprint=fingerprint,
                )
            except ActionPolicyError as exc:
                return ActionDesignResult(outcome="rejected", message=str(exc))
            prior.status = "pending"
            prior.review_decision = None
            prior.review_reason = None
            prior.approved_at = None
            prior.action_id = None
            prior.design_json = request.model_dump_json()
            prior.reason = request.reason
            prior.source = source
            await db.flush()
            return ActionDesignResult(
                outcome="pending_review",
                proposal_id=prior.id,
                message="原提案将重新评审",
            )

        try:
            await check_can_accept(
                db,
                user_id,
                source=source,
                duration_seconds=request.duration_seconds,
                pack_id=pack.id,
                semantic_fingerprint=fingerprint,
            )
        except ActionPolicyError as exc:
            return ActionDesignResult(outcome="rejected", message=str(exc))

        proposal = ActionProposal(
            user_id=user_id,
            pack_id=pack.id,
            source=source,
            source_message_ids=json.dumps(source_message_ids or [], ensure_ascii=False),
            reason=request.reason,
            semantic_fingerprint=fingerprint,
            design_json=request.model_dump_json(),
            # 幂等键限定到包内：A 包与 B 包、抑制期满重提互不冲突。
            idempotency_key=idempotency_key
            or hashlib.sha256(
                f"{pack.id}|{source}|{fingerprint}".encode(),
            ).hexdigest()[:64],
            status="pending",
        )
        db.add(proposal)
        await db.flush()

    return ActionDesignResult(
        outcome="pending_review",
        proposal_id=proposal.id,
        message="提案已受理，将由独立评审决定是否制作",
    )
