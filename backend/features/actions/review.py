"""独立评审：提案与评审分离，不自我批准。评审在提案事务提交后后台执行；失败落 deferred 可重试，不默认批准。approve 才占用制作额度并冻结设计规格到动作行。"""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from infrastructure.ai.error_classifier import FailoverReason, LlmCallBlockedError, LLMRuntimeError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets.asset_store import build_data_uri, image_mime_for_extension, sniff_media_ext
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import utc_now
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.design import action_key_from_name
from features.actions.generation.errors import VideoPackError
from features.actions.generation.references import require_pack_generation_identity
from features.actions.generation.state import GenerationContext
from features.actions.materials import accepted_action_asset
from features.actions.models import ActionProposal, CompanionAction, CompanionActionPack
from features.actions.policy import ActionPolicyError, _reserve_create_slot_row, is_expression_action
from features.actions.prompts import ACTION_REVIEW_INSTRUCTIONS, ACTION_REVIEW_REPAIR_INSTRUCTIONS
from features.actions.repository import ActionNameConflictError, create_action, get_action_by_name, list_pack_actions
from features.actions.schemas import ActionDesignRequest

# 与 ACTION_REVIEW_INSTRUCTIONS 中 existing_actions 的条数上限保持一致。
_EXISTING_ACTION_LIMIT = 10
_REVIEWABLE_STATUSES = ("pending", "deferred")


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reuse", "defer", "reject"]
    reason: str = Field(min_length=1, max_length=400)
    reuse_action_id: int | None = Field(default=None, strict=True, gt=0)


@dataclass(frozen=True)
class ReviewOutcome:
    """已落库的评审结论；approve 时由调用方释放受理锁后启动制作。"""

    decision: str
    pack_id: int
    action_id: int | None


async def _review_payload(
    db: AsyncSession,
    proposal: ActionProposal,
    pack: CompanionActionPack,
    design: ActionDesignRequest,
) -> dict[str, Any]:
    """评审资料：提案、冻结外形/着装与生成上下文中的人设，以及同包现有表达动作。"""
    existing_actions = await _existing_actions(db, pack.id, design)
    character_data: dict[str, Any] = json.loads(pack.character_snapshot)
    if not isinstance(character_data, dict) or not isinstance(character_data.get("profile"), str):
        raise ValueError("动作评审缺少冻结角色资料")
    if pack.context_json is None:
        raise ValueError("动作评审缺少冻结生成上下文")
    context = GenerationContext.model_validate_json(pack.context_json)
    character_data["persona_definition"] = context.persona_definition
    character_data["personality_tags"] = context.personality_tags
    outfit_data = json.loads(pack.outfit_snapshot)
    if not isinstance(outfit_data, dict) or not isinstance(outfit_data.get("description"), str):
        raise ValueError("动作评审缺少冻结着装资料")
    return {
        "design": design.model_dump(),
        "reason": proposal.reason,
        "source": proposal.source,
        "character_snapshot": character_data,
        "outfit_snapshot": outfit_data,
        "existing_actions": existing_actions,
    }


def _similarity_score(design: ActionDesignRequest, action: CompanionAction) -> float:
    """粗粒度语义相近度：名称/描述/用途的词面重叠，供候选排序。"""
    query = " ".join(
        [
            design.name,
            design.motion_description,
            " ".join(design.use_when),
        ],
    ).lower()
    if not query.strip():
        return 0.0
    doc = " ".join([action.name, action.motion_description, action.use_when]).lower()
    tokens = _match_terms(query)
    if not tokens:
        return 0.0
    hit = sum(1 for t in tokens if t in doc)
    return hit / len(tokens)


def _match_terms(text: str) -> set[str]:
    """词面匹配单元：ASCII 连续字母数字按词，中文等不分词的文字按相邻二字切分，否则整句成一个词而匹配不到近似描述。"""
    terms: set[str] = set()
    for chunk in re.split(r"[^\w]+", text):
        if chunk.isascii():
            if len(chunk) >= 2:
                terms.add(chunk)
        else:
            terms.update(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return terms


async def _existing_actions(
    db: AsyncSession,
    pack_id: int,
    design: ActionDesignRequest,
) -> list[dict]:
    """同包表达动作按与提案的词面相近度排序，超上限只留最相近的。词面相近度对中文近义不敏感，不能据此剔除零分动作，否则漏看可复用近义动作。"""
    scored = [
        (_similarity_score(design, action), action)
        for action in await list_pack_actions(db, pack_id, enabled_only=True)
        if is_expression_action(action)
    ]
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [
        {
            "id": action.id,
            "name": action.name,
            "key": action.key,
            "motion_description": action.motion_description,
            "use_when": json.loads(action.use_when or "[]"),
            "avoid_when": json.loads(action.avoid_when or "[]"),
            "duration_seconds": (action.actual_duration_ms or 0) / 1000 or action.target_duration_seconds,
            "kind": action.kind,
            "similarity": round(score, 3),
        }
        for score, action in scored[:_EXISTING_ACTION_LIMIT]
    ]


async def _reviewable_proposal(db: AsyncSession, proposal_id: int, user_id: int) -> ActionProposal | None:
    proposal = await db.get(ActionProposal, proposal_id)
    if proposal is None or proposal.user_id != user_id or proposal.status not in _REVIEWABLE_STATUSES:
        return None
    return proposal


async def review_proposal(proposal_id: int, user_id: int) -> ReviewOutcome | None:
    """独立 LLM 评审并提交结论；提案或其形象已不存在、或已不在待评审状态时返回 None。短会话读取资料 → 不占会话调用模型 → 短会话复核状态后写入；格式失败最多修复一次，仍失败则 defer。同包已有同 key 动作时不调用模型，按其状态复用或暂缓。"""
    async with SESSION_LOCAL() as db:
        proposal = await _reviewable_proposal(db, proposal_id, user_id)
        pack = await db.get(CompanionActionPack, proposal.pack_id) if proposal is not None else None
        if proposal is None or pack is None:
            return None
        design = ActionDesignRequest.model_validate_json(proposal.design_json)
        if (resolved := await _same_key_verdict(db, proposal, design)) is not None:
            return await _commit_verdict(db, proposal, resolved, design)
        if not pack.reference_path:
            raise ValueError("动作评审缺少该形象的参考图")
        reference_path = Path(SETTINGS.data_dir) / pack.reference_path
        payload = await _review_payload(db, proposal, pack, design)

    reference_bytes = await asyncio.to_thread(reference_path.read_bytes)
    mime = image_mime_for_extension(sniff_media_ext(reference_bytes) or "")
    if mime is None:
        raise ValueError("动作评审参考图格式无效")
    verdict = await _request_verdict(
        user_id,
        pack.id,
        payload,
        await asyncio.to_thread(build_data_uri, reference_bytes, mime),
    )

    async with SESSION_LOCAL() as db:
        if (proposal := await _reviewable_proposal(db, proposal_id, user_id)) is None:
            return None
        if isinstance(verdict, ReviewVerdict):
            return await _commit_verdict(db, proposal, verdict, design)
        proposal.review_decision = "defer"
        proposal.review_reason = f"评审格式失败：{verdict}"
        proposal.status = "deferred"
        await db.commit()
        return ReviewOutcome("defer", proposal.pack_id, None)


async def defer_failed_review(proposal_id: int, user_id: int) -> None:
    """评审执行失败时落为可重试的暂缓；新开会话写入，不受失败会话待回滚状态影响。"""
    async with SESSION_LOCAL() as db:
        if (proposal := await _reviewable_proposal(db, proposal_id, user_id)) is None:
            return
        proposal.status = "deferred"
        proposal.review_reason = "评审执行失败，可重试"
        await db.commit()


async def _request_verdict(
    user_id: int,
    pack_id: int,
    payload: dict[str, Any],
    reference_image: str,
) -> ReviewVerdict | str:
    """独立评审与共享格式修复；结构仍无效时返回可展示的简短原因。"""

    async def before_submit() -> None:
        require_new_generation_call(user_id)
        async with SESSION_LOCAL() as db:
            try:
                await require_pack_generation_identity(db, user_id, pack_id)
            except VideoPackError as exc:
                raise LlmCallBlockedError(str(exc)) from exc
        require_new_generation_call(user_id)

    def parse_output(value: Any) -> ReviewVerdict:
        verdict = ReviewVerdict.model_validate(value)
        if verdict.decision == "reuse":
            if verdict.reuse_action_id not in {action["id"] for action in payload["existing_actions"]}:
                raise LlmJsonValidationError("invalid_reuse_action_id", fields=("reuse_action_id",))
        elif verdict.reuse_action_id is not None:
            raise LlmJsonValidationError("unexpected_reuse_action_id", fields=("reuse_action_id",))
        return verdict

    llm_config = await load_user_llm_config(
        user_id,
        AiTaskKey.window_action,
        required_modalities=frozenset({ModelModality.text, ModelModality.image}),
    )
    try:
        return await call_llm_json(
            llm_config,
            ACTION_REVIEW_INSTRUCTIONS,
            payload,
            schema=ReviewVerdict.model_json_schema(),
            schema_name="action_review",
            parse_output=parse_output,
            repair_prompt=ACTION_REVIEW_REPAIR_INSTRUCTIONS,
            reference_images=(reference_image,),
            preserve_choices=("decision",),
            before_call=before_submit,
        )
    except LLMRuntimeError as exc:
        if exc.classified.reason != FailoverReason.format_error:
            raise
        return "未通过结构校验"


async def _commit_verdict(
    db: AsyncSession,
    proposal: ActionProposal,
    verdict: ReviewVerdict,
    design: ActionDesignRequest,
) -> ReviewOutcome:
    decision = await _apply_verdict(db, proposal, verdict, design)
    await db.commit()
    return ReviewOutcome(decision, proposal.pack_id, proposal.action_id)


async def _same_key_verdict(
    db: AsyncSession,
    proposal: ActionProposal,
    design: ActionDesignRequest,
) -> ReviewVerdict | None:
    """同包已有同 key 动作时的确定结论：就绪即复用，其余暂缓，不覆盖或重排。同名即同一动作身份；两个同名提案先后获批时后者按先者状态收敛。"""
    try:
        existing = await get_action_by_name(db, proposal.pack_id, design.name)
    except ActionNameConflictError as exc:
        return ReviewVerdict(decision="defer", reason=str(exc))
    if existing is None:
        return None
    name = existing.name or existing.key
    if accepted_action_asset(existing) is not None:
        return ReviewVerdict(
            decision="reuse",
            reason=f"同名动作「{name}」已就绪，按复用处理",
            reuse_action_id=existing.id,
        )
    return ReviewVerdict(
        decision="defer",
        reason=f"同名动作「{name}」已有制作记录，暂缓以免覆盖；可换一个名称，或待该动作完成、重做或删除后再提",
    )


async def _apply_verdict(
    db: AsyncSession,
    proposal: ActionProposal,
    verdict: ReviewVerdict,
    design: ActionDesignRequest,
) -> str:
    """落库评审结论并返回最终结论：批准前再核对同 key 动作，额度不足转暂缓。"""
    if verdict.decision == "approve" and (resolved := await _same_key_verdict(db, proposal, design)) is not None:
        verdict = resolved
    proposal.review_decision = verdict.decision
    proposal.review_reason = verdict.reason

    if verdict.decision == "approve":
        # 制作额度校验：approve 计数由聚合决定；超限回退 defer，不默认批准。
        try:
            creation = await _reserve_create_slot_row(
                db,
                proposal.user_id,
                source=proposal.source,
                creation_key=f"proposal:{proposal.id}",
            )
        except ActionPolicyError as exc:
            proposal.review_decision = "defer"
            proposal.review_reason = f"额度不足：{exc}"
            proposal.status = "deferred"
            await db.flush()
            return "defer"
        proposal.status = "approved"
        proposal.approved_at = utc_now()
        action = await create_action(
            db,
            user_id=proposal.user_id,
            pack_id=proposal.pack_id,
            key=action_key_from_name(design.name),
            name=design.name,
            kind=design.clip_kind,
            motion_description=design.motion_description,
            use_when=design.use_when,
            avoid_when=design.avoid_when,
        )
        action.status = "queued"
        action.stage = "design"
        action.target_duration_seconds = design.duration_seconds
        action.loopable = design.clip_kind == "loop"
        # 冻结提案规格：生成编排按此演绎，不回读提案表。
        action.source_design_json = proposal.design_json
        proposal.action_id = action.id
        creation.action_id = action.id
    elif verdict.decision == "reuse":
        proposal.status = "reused"
        proposal.action_id = verdict.reuse_action_id
    elif verdict.decision == "defer":
        proposal.status = "deferred"
    else:
        # reject 状态提案即为 7 天抑制记录，无需冗余抑制表。
        proposal.status = "rejected"

    await db.flush()
    return verdict.decision
