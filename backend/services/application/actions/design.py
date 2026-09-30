"""提案受理与语义去重：reused / pending_review / rejected。

受理锁内依次处理：同 key 动作（就绪复用、在制或待确认直接返回、失败或取消原位重做）→ 同创意在审提案去重
→ 门禁与抠像模型检查 → 复用 deferred / rejected 原提案行或新建提案并 flush。
调用方在锁外提交，并发新建的同创意提案由幂等唯一约束兜底；幂等键限定 (user, source, pack, fingerprint)，
换包或抑制期满后同创意可重提。
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from modules.companion import ActionDesignRequest, ActionDesignResult, ActionProposal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.generation.video import VideoPackStateError, require_action_matting_model
from services.domains.actions import (
    ActionPolicyError,
    check_can_accept,
    get_action_accept_lock,
    get_action_by_key,
    get_active_pack,
    make_semantic_fingerprint,
)

# 动作 key：小写 ASCII slug；非 ASCII（如中文名）按语义指纹派生，保证
# 「拥抱」「打哈欠」「跳舞」映射到不同稳定 key。
_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ProposalAcceptance:
    """受理结论；调用方提交受理事务后交给 `schedule_accepted_proposal` 启动后台工作。"""

    result: ActionDesignResult
    # 同 key 动作原位重做或仍在制作时需唤醒生成的所属包；其余结论为空。
    wake_pack_id: int | None = None


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
    source: str,
) -> ProposalAcceptance:
    """受理提案；不阻塞等待评审与视频。"""
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return ProposalAcceptance(ActionDesignResult(outcome="rejected", message="当前没有可用的形象动作"))

    if request.expected_pack_id is not None and request.expected_pack_id != pack.id:
        return ProposalAcceptance(ActionDesignResult(outcome="rejected", message="形象已切换，请刷新动作列表"))

    async with get_action_accept_lock(user_id):
        return await _accept_in_pack(db, user_id, pack.id, request, source=source)


async def _accept_in_pack(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    request: ActionDesignRequest,
    *,
    source: str,
) -> ProposalAcceptance:
    """受理锁内：同 key 动作复用、等待或重做 → 同创意提案去重或重审 → 门禁后新建提案。"""
    fingerprint = make_semantic_fingerprint(request.name, request.motion_description)
    existing = await get_action_by_key(db, pack_id, action_key_from_name(request.name, fingerprint))
    if existing and existing.status == "succeeded" and existing.video_path:
        return ProposalAcceptance(
            ActionDesignResult(
                outcome="reused",
                action_id=existing.id,
                message=f"已有动作「{existing.name}」，请核对其内容与启用状态后复用",
            ),
        )
    if existing and existing.status in ("queued", "processing", "result_unknown"):
        return ProposalAcceptance(
            ActionDesignResult(
                outcome="pending_review",
                action_id=existing.id,
                message=f"相似动作「{existing.name}」已在制作中",
            ),
            wake_pack_id=pack_id,
        )
    if existing and existing.status == "review":
        # 成品待用户复核：不重做也不新建提案，采纳后才进入可播目录。
        return ProposalAcceptance(
            ActionDesignResult(
                outcome="pending_review",
                action_id=existing.id,
                message=f"相似动作「{existing.name}」已制作完成，等待用户确认后才能播放",
            ),
        )
    if existing and existing.status in ("failed", "cancelled"):
        try:
            require_action_matting_model()
        except VideoPackStateError as exc:
            return ProposalAcceptance(ActionDesignResult(outcome="rejected", message=str(exc)))
        # 制作失败的同名动作：保留动作身份重做素材，不新建提案。
        existing.status = "queued"
        existing.stage = "design"
        existing.error = None
        await db.flush()
        return ProposalAcceptance(
            ActionDesignResult(
                outcome="pending_review",
                action_id=existing.id,
                message=f"将重新制作动作「{existing.name}」",
            ),
            wake_pack_id=pack_id,
        )

    pending_id = await db.scalar(
        select(ActionProposal.id).where(
            ActionProposal.user_id == user_id,
            ActionProposal.pack_id == pack_id,
            ActionProposal.semantic_fingerprint == fingerprint,
            ActionProposal.status == "pending",
        ),
    )
    if pending_id is not None:
        return ProposalAcceptance(
            ActionDesignResult(
                outcome="pending_review",
                proposal_id=pending_id,
                message="已有相同创意的提案在评审中",
            ),
        )

    prior = await db.scalar(
        select(ActionProposal)
        .where(
            ActionProposal.user_id == user_id,
            ActionProposal.pack_id == pack_id,
            ActionProposal.semantic_fingerprint == fingerprint,
            ActionProposal.status.in_(("deferred", "rejected", "approved")),
        )
        .order_by(ActionProposal.id.desc()),
    )
    try:
        await check_can_accept(
            db,
            user_id,
            source=source,
            duration_seconds=request.duration_seconds,
            pack_id=pack_id,
            semantic_fingerprint=fingerprint,
        )
        require_action_matting_model()
    except (ActionPolicyError, VideoPackStateError) as exc:
        return ProposalAcceptance(ActionDesignResult(outcome="rejected", message=str(exc)))

    if prior is not None and prior.status in ("deferred", "rejected"):
        # deferred / 拒绝抑制期满后的原创意重提：复用原提案行再评审，不撞幂等唯一约束。
        prior.status = "pending"
        prior.review_decision = None
        prior.review_reason = None
        prior.approved_at = None
        prior.action_id = None
        prior.design_json = request.model_dump_json()
        prior.reason = request.reason
        prior.source = source
        await db.flush()
        return ProposalAcceptance(
            ActionDesignResult(
                outcome="pending_review",
                proposal_id=prior.id,
                message="原提案将重新评审",
            ),
        )

    proposal = ActionProposal(
        user_id=user_id,
        pack_id=pack_id,
        source=source,
        reason=request.reason,
        semantic_fingerprint=fingerprint,
        design_json=request.model_dump_json(),
        # 幂等键限定到包内：A 包与 B 包、抑制期满重提互不冲突。
        idempotency_key=hashlib.sha256(f"{pack_id}|{source}|{fingerprint}".encode()).hexdigest()[:64],
        status="pending",
    )
    db.add(proposal)
    await db.flush()
    return ProposalAcceptance(
        ActionDesignResult(
            outcome="pending_review",
            proposal_id=proposal.id,
            message="提案已受理，将由独立评审决定是否制作",
        ),
    )
