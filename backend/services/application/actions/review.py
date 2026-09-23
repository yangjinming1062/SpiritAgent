"""独立评审：提案与评审调用分离，不自我批准。

评审在提案事务提交后的后台执行；失败落 deferred 可重试，不默认批准。
approve 才占用制作额度，并把设计规格冻结到动作行供生成编排读取。
"""

import asyncio
import json
import re
from pathlib import Path

from components import SETTINGS, parse_llm_json, utc_now
from modules.companion import (
    ActionProposal,
    CharacterCardSnapshot,
    CompanionAction,
    CompanionActionPack,
    Persona,
)
from prompts.actions import ACTION_REVIEW_INSTRUCTIONS
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import (
    ActionPolicyError,
    consume_create_slot,
    list_pack_actions,
    upsert_action,
)
from services.domains.companion import render_character_profile
from services.infrastructure.assets import build_data_uri, sniff_media_ext
from services.infrastructure.llm import vision_chat

from .design import action_key_from_name


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(approve|reuse|defer|reject)$")
    reason: str = Field(max_length=400)
    reuse_action_id: int | None = None


async def _review_payload(db: AsyncSession, user_id: int, proposal: ActionProposal, pack: CompanionActionPack) -> dict:
    """评审资料：提案、性格与冻结外形/着装，以及同包相似动作候选。"""
    design = json.loads(proposal.design_json or "{}")
    candidates = await _similar_action_candidates(db, pack.id, design)
    character_data: dict = {}
    raw_character = (pack.character_snapshot or "").strip()
    if raw_character and raw_character != "{}":
        character_data = json.loads(raw_character)
    context = json.loads(pack.context_json or "{}") if pack.context_json else {}
    if not character_data.get("profile") and context.get("identity"):
        character_data["profile"] = render_character_profile(CharacterCardSnapshot.model_validate(context["identity"]))
    # 固定外形只取本包快照；人设缺失时才补充实时性格资料。
    if "persona_definition" in context:
        character_data["persona_definition"] = context["persona_definition"]
    if "personality_tags" in context:
        character_data["personality_tags"] = context["personality_tags"]
    if "persona_definition" not in character_data or "personality_tags" not in character_data:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is not None:
            definition = json.loads(persona.definition_json or "{}")
            character_data.setdefault(
                "persona_definition",
                {
                    key: value
                    for key, value in definition.items()
                    if key in ("name", "personality", "speaking_style", "relationship")
                },
            )
            character_data.setdefault("personality_tags", json.loads(persona.personality_tags_json or "[]"))
    outfit_data: dict = {}
    raw_outfit = (pack.outfit_snapshot or "").strip()
    if raw_outfit and raw_outfit != "{}":
        try:
            outfit_data = json.loads(raw_outfit)
        except json.JSONDecodeError:
            outfit_data = {}
    return {
        "design": design,
        "reason": proposal.reason,
        "source": proposal.source,
        "character_snapshot": character_data,
        "outfit_snapshot": outfit_data,
        "candidates": candidates[:10],
    }


def _similarity_score(design: dict, action: CompanionAction) -> float:
    """粗粒度语义相近度：名称/描述/用途/标签的词面重叠，供候选排序。"""
    query = " ".join(
        [
            str(design.get("name", "")),
            str(design.get("motion_description", "")),
            " ".join(str(x) for x in design.get("use_when", []) or []),
            " ".join(str(x) for x in design.get("tags", []) or []),
        ],
    ).lower()
    if not query.strip():
        return 0.0
    doc = " ".join(
        [
            action.name or "",
            action.motion_description or "",
            action.use_when or "",
            action.tags or "",
        ],
    ).lower()
    tokens = {t for t in re.split(r"[^\w]+", query) if len(t) >= 2}
    if not tokens:
        return 0.0
    hit = sum(1 for t in tokens if t in doc)
    return hit / len(tokens)


async def _similar_action_candidates(
    db: AsyncSession,
    pack_id: int,
    design: dict,
) -> list[dict]:
    """按提案内容检索相近的已就绪动作，避免固定顺序把近义动作挤出评审上下文。"""
    scored: list[tuple[float, CompanionAction]] = []
    for action in await list_pack_actions(db, pack_id, enabled_only=True):
        if action.status == "succeeded" and action.video_path and not action.system_slot:
            scored.append((_similarity_score(design, action), action))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    candidates = []
    for score, action in scored[:10]:
        if score <= 0 and len(candidates) >= 5:
            continue
        candidates.append(
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
            },
        )
    return candidates


async def review_proposal(
    db: AsyncSession,
    user_id: int,
    proposal: ActionProposal,
    *,
    identity_prompt: str = "",
    reference_image: str = "",
) -> str:
    """独立 LLM 评审；格式失败最多修复一次，仍失败则 defer。"""
    pack = await db.get(CompanionActionPack, proposal.pack_id)
    if pack is None:
        return "reject"

    if not reference_image:
        if not pack.reference_path:
            raise ValueError("动作评审缺少该形象的参考图")
        reference_bytes = await asyncio.to_thread((Path(SETTINGS.data_dir) / pack.reference_path).read_bytes)
        mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(
            sniff_media_ext(reference_bytes) or "",
        )
        if mime is None:
            raise ValueError("动作评审参考图格式无效")
        reference_image = build_data_uri(reference_bytes, mime)

    payload = await _review_payload(db, user_id, proposal, pack)
    if not proposal.candidate_action_ids:
        # 首次评审时冻结候选快照（记录留档）；重试沿用最新目录重算候选。
        proposal.candidate_action_ids = json.dumps(
            [c["id"] for c in payload["candidates"]],
            ensure_ascii=False,
        )
        await db.flush()

    last_error = "评审失败"
    for _attempt in range(2):
        try:
            raw = await vision_chat(
                user_id,
                ACTION_REVIEW_INSTRUCTIONS + ("\n" + identity_prompt if identity_prompt else ""),
                json.dumps(payload, ensure_ascii=False),
                reference_images=(reference_image,),
            )
            verdict = ReviewVerdict.model_validate(parse_llm_json(raw))
            if verdict.decision == "reuse":
                if verdict.reuse_action_id not in {candidate["id"] for candidate in payload["candidates"]}:
                    raise ValueError("reuse_action_id 必须取自 candidates 的 id")
            elif verdict.reuse_action_id is not None:
                raise ValueError("非 reuse 结论的 reuse_action_id 必须为 null")
            await _apply_verdict(db, user_id, proposal, verdict)
            return verdict.decision
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            payload["validation_error"] = last_error

    proposal.review_decision = "defer"
    proposal.review_reason = f"评审格式失败：{last_error}"
    proposal.status = "deferred"
    await db.flush()
    return "defer"


async def _apply_verdict(
    db: AsyncSession,
    user_id: int,
    proposal: ActionProposal,
    verdict: ReviewVerdict,
) -> None:
    proposal.review_decision = verdict.decision
    proposal.review_reason = verdict.reason

    if verdict.decision == "approve":
        design = json.loads(proposal.design_json or "{}")
        fingerprint = proposal.semantic_fingerprint or ""
        key = action_key_from_name(str(design.get("name", "action")), fingerprint)
        # 制作额度校验：approve 计数由聚合决定；超限回退 defer，不默认批准。
        try:
            await consume_create_slot(db, user_id, source=proposal.source)
        except ActionPolicyError as exc:
            proposal.review_decision = "defer"
            proposal.review_reason = f"额度不足：{exc}"
            proposal.status = "deferred"
            await db.flush()
            return
        proposal.status = "approved"
        proposal.approved_at = utc_now()
        action = await upsert_action(
            db,
            pack_id=proposal.pack_id,
            key=key,
            name=str(design.get("name", "未命名动作")),
            kind=str(design.get("clip_kind", "once")),
            motion_description=str(design.get("motion_description", "")),
            use_when=list(design.get("use_when", []) or []),
            avoid_when=list(design.get("avoid_when", []) or []),
            tags=list(design.get("tags", []) or []),
        )
        action.status = "queued"
        action.stage = "design"
        action.target_duration_seconds = float(design.get("duration_seconds", 2.0) or 2.0)
        action.loopable = str(design.get("clip_kind", "once")) == "loop"
        # 冻结提案规格：生成编排按此演绎，不回读提案表。
        action.source_design_json = proposal.design_json
        proposal.action_id = action.id
    elif verdict.decision == "reuse":
        proposal.status = "reused"
        proposal.action_id = verdict.reuse_action_id
    elif verdict.decision == "defer":
        proposal.status = "deferred"
    else:
        # reject 状态提案即为 7 天抑制记录，无需冗余抑制表。
        proposal.status = "rejected"

    await db.flush()
