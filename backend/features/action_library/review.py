"""共享创意只评审一次，画面适配和素材质量由各制作链负责。"""

import asyncio
import re
from typing import Any, Literal

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets.asset_store import build_data_uri
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call

from features.appearance.avatar_service import read_portrait_bytes

from .identity import definition_matches_source, same_review_reference
from .ports import read_source
from .prompts import ACTION_DEFINITION_REVIEW, ACTION_DEFINITION_REVIEW_REPAIR
from .queries import definition_snapshot, definition_with_source, matching_definitions
from .schemas import ActionDefinitionSnapshot
from .service import begin_review, record_review

logger = get_logger(__name__)
_REVIEW_LOCKS: dict[tuple[int, int | None, str], asyncio.Lock] = {}


class DefinitionReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reuse", "defer", "reject"]
    reason: str = Field(min_length=1, max_length=400)
    reuse_definition_id: int | None = Field(default=None, strict=True, gt=0)


def _similarity_score(design: ActionDefinitionSnapshot, candidate: ActionDefinitionSnapshot) -> float:
    query = " ".join((design.name, design.motion_description, *design.use_when)).casefold()
    terms: set[str] = set()
    for chunk in re.split(r"[^\w]+", query):
        if chunk.isascii():
            if len(chunk) >= 2:
                terms.add(chunk)
        else:
            terms.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    if not terms:
        return 0.0
    text = " ".join((candidate.name, candidate.motion_description, *candidate.use_when)).casefold()
    return sum(term in text for term in terms) / len(terms)


async def review_definition(user_id: int, definition_id: int) -> None:
    async with SESSION_LOCAL() as db:
        definition = await definition_snapshot(db, user_id, definition_id)
    if definition is None or definition.status != "pending":
        return
    key = (user_id, definition.outfit_id, definition.source_visual_key)
    async with _REVIEW_LOCKS.setdefault(key, asyncio.Lock()):
        await _review_definition_locked(user_id, definition_id)


async def _review_definition_locked(user_id: int, definition_id: int) -> None:
    async with SESSION_LOCAL() as db:
        loaded = await definition_with_source(db, user_id, definition_id)
        if loaded is None:
            return
        definition, source = loaded
        if definition.status != "pending":
            return
        candidates = [
            item
            for item in await matching_definitions(db, source)
            if item.id != definition_id and item.status == "approved"
        ]
        candidates.sort(key=lambda item: (-_similarity_score(definition, item), item.id))
        candidates = candidates[:10]

    async def before_call() -> None:
        require_new_generation_call(user_id)
        if definition.source == "autonomous" and not SETTINGS.action_autocreate_enabled:
            raise LlmCallBlockedError("自主创建新动作当前已关闭")
        async with SESSION_LOCAL() as db:
            current = await definition_snapshot(db, user_id, definition_id)
            current_source = await read_source(db, user_id, source.target_id)
            if current is None or current.status != "reviewing" or current_source is None or not current_source.ready:
                raise LlmCallBlockedError("动作评审来源已不可用")
            if not definition_matches_source(current, current_source):
                raise LlmCallBlockedError("动作评审的冻结来源已变化")
            if source.scope == "target" and not same_review_reference(source, current_source):
                raise LlmCallBlockedError("桌景的确认画面或着装资料已变化，请重新评审")

    def parse_output(value: Any) -> DefinitionReviewVerdict:
        verdict = DefinitionReviewVerdict.model_validate(value)
        if verdict.decision == "reuse":
            if verdict.reuse_definition_id not in {item.id for item in candidates}:
                raise LlmJsonValidationError("invalid_reuse_definition_id", fields=("reuse_definition_id",))
        elif verdict.reuse_definition_id is not None:
            raise LlmJsonValidationError("unexpected_reuse_definition_id", fields=("reuse_definition_id",))
        return verdict

    references: list[str] = []
    for path in source.reference_paths:
        loaded = await asyncio.to_thread(read_portrait_bytes, path)
        if loaded is None:
            raise ValueError("共享动作的冻结参考图不可读")
        references.append(await asyncio.to_thread(build_data_uri, loaded[0], loaded[1]))
    if not references:
        raise ValueError("共享动作缺少冻结参考图")
    config = await load_user_llm_config(
        user_id,
        AiTaskKey.desktop_life,
        required_modalities=frozenset({ModelModality.text, ModelModality.image}),
    )
    payload = {
        "creation_scope": "current_desktop" if source.scope == "target" else "shared_outfit",
        "design": definition.model_dump(
            include={"name", "motion_description", "use_when", "avoid_when", "clip_kind", "duration_seconds"},
        ),
        "reason": definition.reason,
        "reference_roles": source.reference_roles,
        "identity": source.identity.model_dump() if source.identity else None,
        "outfit_description": source.outfit_description,
        "persona": source.persona,
        "existing_actions": [
            item.model_dump(
                include={"id", "name", "motion_description", "use_when", "avoid_when", "clip_kind", "duration_seconds"},
            )
            for item in candidates
        ],
    }
    async with SESSION_LOCAL() as db:
        if not await begin_review(db, user_id, definition_id):
            return
        await db.commit()
    verdict = await call_llm_json(
        config,
        ACTION_DEFINITION_REVIEW,
        payload,
        schema=DefinitionReviewVerdict.model_json_schema(),
        schema_name="shared_action_review",
        parse_output=parse_output,
        repair_prompt=ACTION_DEFINITION_REVIEW_REPAIR,
        reference_images=tuple(references),
        preserve_choices=("decision",),
        before_call=before_call,
    )
    require_new_generation_call(user_id)
    async with SESSION_LOCAL() as db:
        recorded = await record_review(
            db,
            user_id,
            definition_id,
            verdict.decision,
            verdict.reason,
            verdict.reuse_definition_id,
        )
        await db.commit()
    if recorded is None:
        # 竞态下结论已无法覆盖既有状态，保留诊断避免静默丢弃已计费判决。
        logger.warning(
            "shared action review verdict dropped",
            extra={"definition_id": definition_id, "user_id": user_id, "decision": verdict.decision},
        )
