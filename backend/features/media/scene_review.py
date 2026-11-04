"""场景候选核对是否重复描绘桌面伙伴；识别参考仅进入独立视觉检查。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Literal

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets import asset_store
from infrastructure.assets.asset_store import read_asset_data_uri
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import GenerationWorkPaused

from features.media.image_generation import ImageReviewUnavailableError
from features.media.prompts import SCENE_IMAGE_REVIEW_SYSTEM
from features.media.reference_reader import confirmed_media_reference_paths
from features.media.review_contracts import ReviewDecision, ReviewEvidence, ReviewVerdict, VisualReview
from features.media.review_evidence import freeze_review_evidence, read_review_evidence

logger = get_logger(__name__)


class SceneImageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "duplicate_character", "invalid_environment", "review"]
    reason: str = Field(min_length=1, max_length=1000)
    goal_verdict: ReviewVerdict
    goal_reason: str = Field(min_length=1, max_length=1000)
    quality_verdict: ReviewVerdict
    quality_reason: str = Field(min_length=1, max_length=1000)


async def scene_identity_reference(user_id: int) -> str:
    async with SESSION_LOCAL() as db:
        paths = await confirmed_media_reference_paths(db, user_id)
    for path in paths:
        bare = asset_store.normalize_asset_reference(path)
        parsed = parse_companion_asset_path(bare)
        if parsed is not None and parsed[0] == user_id and (uri := await asyncio.to_thread(read_asset_data_uri, bare)):
            return uri
    raise ImageReviewUnavailableError("场景图片已保留，但伙伴的已确认形象无法读取；请修复形象后重试分析")


async def review_scene_image(
    user_id: int,
    candidate_uri: str,
    *,
    generation_prompt: str,
    reference_uri: str,
    before_submit: Callable[[], Awaitable[None]] | None = None,
    task: AiTaskKey = AiTaskKey.scene,
    evidence: ReviewEvidence | None = None,
    previous: VisualReview | None = None,
    save_evidence: Callable[[ReviewEvidence], Awaitable[None]] | None = None,
) -> VisualReview:
    seen = False
    try:
        if evidence is None:
            companion_uri = reference_uri
            evidence = await freeze_review_evidence(
                user_id,
                (companion_uri, candidate_uri),
                prompt=SCENE_IMAGE_REVIEW_SYSTEM,
                context={
                    "generation_prompt": generation_prompt,
                    "references": [
                        {"image": 1, "role": "reference_character"},
                        {"image": 2, "role": "candidate_environment"},
                    ],
                },
            )
            if save_evidence:
                await save_evidence(evidence)
        config = await load_user_llm_config(
            user_id, task, required_modalities=frozenset({ModelModality.text, ModelModality.image})
        )

        def parse(value: object) -> SceneImageReview:
            nonlocal seen
            seen = True
            return SceneImageReview.model_validate(value)

        payload = await call_llm_json(
            config,
            evidence.policy_prompt,
            json.loads(evidence.context_json),
            schema=SceneImageReview.model_json_schema(),
            schema_name="scene_image_review",
            parse_output=parse,
            repair_prompt="只整理原返回格式及已有依据，保留 decision、goal_verdict、quality_verdict，不重新判断或补造视觉事实。",
            reference_images=await read_review_evidence(evidence),
            before_call=before_submit,
            preserve_choices=("decision", "goal_verdict", "quality_verdict"),
        )
        decision: ReviewVerdict = (
            "pass" if payload.decision == "accepted" else "review" if payload.decision == "review" else "fail"
        )
        goal = max(
            (
                ReviewDecision(verdict=decision, reason=payload.reason),
                ReviewDecision(verdict=payload.goal_verdict, reason=payload.goal_reason),
            ),
            key=lambda item: {"pass": 0, "review": 1, "fail": 2}[item.verdict],
        )
        quality = ReviewDecision(verdict=payload.quality_verdict, reason=payload.quality_reason)
        if previous:
            if previous.goal and previous.goal.verdict in {"pass", "fail"}:
                goal = previous.goal
            if previous.quality and previous.quality.verdict in {"pass", "fail"}:
                quality = previous.quality
        return VisualReview(
            required=("goal", "quality"),
            goal=goal,
            quality=quality,
            evidence=evidence,
        )
    except GenerationWorkPaused:
        raise
    except Exception as exc:
        logger.warning("scene image review interrupted", extra={"user_id": user_id}, exc_info=True)
        return VisualReview(
            required=("goal", "quality"),
            goal=previous.goal if previous else None,
            quality=previous.quality if previous else None,
            evidence=evidence,
            status="blocked" if isinstance(exc, LlmCallBlockedError) else "invalid" if seen else "unavailable",
        )
