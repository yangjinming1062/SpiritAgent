"""提案受理与语义去重：reused / pending_review / rejected。

受理锁内依次处理：同 key 动作（就绪复用；在制或待用户确认直接返回；失败、取消或复核已结束时原位重做）
→ 同创意在审提案去重 → 最近一次复用结论所指动作仍可播放时直接复用 → 门禁与抠像模型检查
→ 复用 deferred / rejected 原提案行，或新建提案行。已批准与已复用的提案作为历史保留，
同一创意再次制作时新建提案行，每次批准都计入额度。

幂等键在 (user, source, pack, fingerprint) 下取首个未占用的序号；调用方在锁外提交，
并发受理同一创意时由唯一约束去重，落败方返回已受理的提案。
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from modules.companion import ActionDesignRequest, ActionDesignResult, ActionProposal, CompanionAction
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.generation import has_pending_action_review
from services.application.generation.video import VideoPackStateError, require_action_matting_model
from services.domains.actions import (
    ActionPolicyError,
    check_can_accept,
    clear_action_attempt,
    get_action,
    get_action_accept_lock,
    get_action_by_key,
    get_active_pack,
    is_expression_action,
    make_semantic_fingerprint,
)

# 动作 key：小写 ASCII slug；非 ASCII（如中文名）按语义指纹派生，保证
# 「拥抱」「打哈欠」「跳舞」映射到不同稳定 key。
_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")

ExistingActionState = Literal["in_production", "awaiting_review", "redo_requested"]


@dataclass(frozen=True)
class ProposalAcceptance:
    """受理结论及其所属包；调用方提交受理事务后交给 `schedule_accepted_proposal` 启动后台工作。"""

    result: ActionDesignResult
    pack_id: int | None = None
    # 结论落在同 key 已有动作上时的动作状态：在制与重做由调度唤醒生成，待确认只等用户复核。
    existing_action: ExistingActionState | None = None


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
        result, existing_action = await _accept_in_pack(db, user_id, pack.id, request, source=source)
    return ProposalAcceptance(result, pack.id, existing_action)


async def _accept_in_pack(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    request: ActionDesignRequest,
    *,
    source: str,
) -> tuple[ActionDesignResult, ExistingActionState | None]:
    """受理锁内：同 key 动作复用、等待或重做 → 同创意提案去重、沿用复用结论或重审 → 门禁后新建提案。"""
    fingerprint = make_semantic_fingerprint(request.name, request.motion_description)
    existing = await get_action_by_key(db, pack_id, action_key_from_name(request.name, fingerprint))
    if existing is not None and (same_key := await _accept_same_key(db, existing)) is not None:
        return same_key

    pending_id = await db.scalar(
        select(ActionProposal.id).where(
            ActionProposal.user_id == user_id,
            ActionProposal.pack_id == pack_id,
            ActionProposal.semantic_fingerprint == fingerprint,
            ActionProposal.status == "pending",
        ),
    )
    if pending_id is not None:
        return _pending_proposal(pending_id, "已有相同创意的提案在评审中"), None

    prior = await db.scalar(
        select(ActionProposal)
        .where(
            ActionProposal.user_id == user_id,
            ActionProposal.pack_id == pack_id,
            ActionProposal.semantic_fingerprint == fingerprint,
            ActionProposal.status.in_(("deferred", "rejected", "approved", "reused")),
        )
        .order_by(ActionProposal.id.desc())
        .limit(1),
    )
    if prior is not None and prior.status == "reused" and prior.action_id is not None:
        target = await get_action(db, prior.action_id)
        if target is not None and target.pack_id == pack_id and is_expression_action(target):
            return (
                ActionDesignResult(
                    outcome="reused",
                    action_id=target.id,
                    message=f"该创意已评审为复用动作「{target.name}」，请核对其内容后复用",
                ),
                None,
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
        return ActionDesignResult(outcome="rejected", message=str(exc)), None

    # deferred / 拒绝抑制期满后的原创意重提复用原提案行再评审；已批准或已复用而动作已不可用时
    # 保留历史行，新建提案重新评审与制作，批准照常计入额度。
    reopen = prior if prior is not None and prior.status in ("deferred", "rejected") else None
    if reopen is not None and reopen.source == source:
        key = reopen.idempotency_key
    else:
        key = await _free_idempotency_key(db, user_id, pack_id, source, fingerprint)
    try:
        # 保存点内写入：并发受理先登记了同一幂等键时只回滚本次写入。
        async with db.begin_nested():
            if reopen is not None:
                proposal = reopen
                proposal.status = "pending"
                proposal.review_decision = None
                proposal.review_reason = None
                proposal.approved_at = None
                proposal.action_id = None
                proposal.design_json = request.model_dump_json()
                proposal.reason = request.reason
                proposal.source = source
                proposal.idempotency_key = key
            else:
                proposal = ActionProposal(
                    user_id=user_id,
                    pack_id=pack_id,
                    source=source,
                    reason=request.reason,
                    semantic_fingerprint=fingerprint,
                    design_json=request.model_dump_json(),
                    idempotency_key=key,
                    status="pending",
                )
                db.add(proposal)
            await db.flush()
    except IntegrityError:
        concurrent_id = await db.scalar(
            select(ActionProposal.id).where(
                ActionProposal.user_id == user_id,
                ActionProposal.source == source,
                ActionProposal.idempotency_key == key,
            ),
        )
        if concurrent_id is None:
            raise
        return _pending_proposal(concurrent_id, "已有相同创意的提案在评审中"), None
    message = "原提案将重新评审" if reopen is not None else "提案已受理，将由独立评审决定是否制作"
    return _pending_proposal(proposal.id, message), None


async def _accept_same_key(
    db: AsyncSession,
    existing: CompanionAction,
) -> tuple[ActionDesignResult, ExistingActionState | None] | None:
    """同 key 已有动作时按其状态受理；返回 None 表示交给提案流程。"""
    name = existing.name
    if existing.status == "succeeded" and existing.video_path:
        return (
            ActionDesignResult(
                outcome="reused",
                action_id=existing.id,
                message=f"已有动作「{name}」，请核对其内容与启用状态后复用",
            ),
            None,
        )
    if existing.status in ("queued", "processing", "result_unknown"):
        return _pending_action(existing.id, f"相似动作「{name}」已在制作中"), "in_production"
    if existing.status == "review" and await has_pending_action_review(db, existing):
        return _pending_action(existing.id, f"相似动作「{name}」已制作完成，等待用户确认后才能播放"), "awaiting_review"
    if existing.status not in ("failed", "cancelled", "review"):
        return None
    try:
        require_action_matting_model()
    except VideoPackStateError as exc:
        return ActionDesignResult(outcome="rejected", message=str(exc)), None
    if existing.status == "review":
        # 复核项已结束的成品视同未采纳，作废后独立重做；用户拒绝的成品在拒绝时已作废。
        clear_action_attempt(existing)
    # 失败、取消或未被采纳的同名动作：保留动作身份原位重做，不新建提案。
    existing.status = "queued"
    existing.stage = "design"
    existing.error = None
    await db.flush()
    return _pending_action(existing.id, f"将重新制作动作「{name}」"), "redo_requested"


def _pending_action(action_id: int, message: str) -> ActionDesignResult:
    return ActionDesignResult(outcome="pending_review", action_id=action_id, message=message)


def _pending_proposal(proposal_id: int, message: str) -> ActionDesignResult:
    return ActionDesignResult(outcome="pending_review", proposal_id=proposal_id, message=message)


def _idempotency_key(pack_id: int, source: str, fingerprint: str, sequence: int) -> str:
    raw = f"{pack_id}|{source}|{fingerprint}"
    if sequence:
        raw += f"|{sequence}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


async def _free_idempotency_key(
    db: AsyncSession,
    user_id: int,
    pack_id: int,
    source: str,
    fingerprint: str,
) -> str:
    """该来源下同一创意首个未占用的幂等键；同时受理同一创意的请求会算出同一个键，由唯一约束去重。"""
    used = set(
        (
            await db.scalars(
                select(ActionProposal.idempotency_key).where(
                    ActionProposal.user_id == user_id,
                    ActionProposal.source == source,
                    ActionProposal.pack_id == pack_id,
                    ActionProposal.semantic_fingerprint == fingerprint,
                ),
            )
        ).all(),
    )
    sequence = 0
    while (key := _idempotency_key(pack_id, source, fingerprint, sequence)) in used:
        sequence += 1
    return key
