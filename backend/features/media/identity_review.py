"""角色媒体的可见身份核查及图片目标审阅；不改写候选图。"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Literal

from infrastructure.ai.error_classifier import FailoverReason, LlmCallBlockedError, classify_api_error
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json, vision_chat
from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import UserLlmConfig, load_user_llm_config
from infrastructure.assets.asset_store import build_data_uri
from infrastructure.async_io import run_blocking
from infrastructure.logger import get_logger
from infrastructure.text import parse_llm_json
from infrastructure.video_processing.frames import action_frame_review_input, measure_action_frame
from pydantic import BaseModel, ConfigDict, Field

from features.media.chain import MEDIA_IDENTITY_ACCEPT_SCORE
from features.media.prompts import (
    CHARACTER_IMAGE_GOAL_REVIEW,
    CHARACTER_IMAGE_GOAL_REVIEW_REPAIR,
    CHARACTER_MEDIA_IMAGE_SCORE,
    CHARACTER_MEDIA_SELF_IMAGE_SCORE,
    CHARACTER_MEDIA_VIDEO_SCORE,
)

logger = get_logger(__name__)


class ImageGoalReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: Literal["pass", "review", "fail"]
    reason: str = Field(min_length=1, max_length=400)
    quality_verdict: Literal["pass", "review", "fail"] | None = None


class _CharacterImageReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    score: int = Field(strict=True, ge=0, le=100)
    identity_reason: str = Field(min_length=1, max_length=400)
    goal_verdict: Literal["pass", "review", "fail"]
    goal_reason: str = Field(min_length=1, max_length=400)
    quality_verdict: Literal["pass", "review", "fail"]
    quality_reason: str = Field(min_length=1, max_length=400)


class ImageGoalReviewFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["unavailable", "invalid", "blocked"]
    score: int | None = Field(default=None, strict=True, ge=0, le=100)
    verdict: Literal["pass", "review", "fail"] | None = None
    quality_verdict: Literal["pass", "review", "fail"] | None = None


@dataclass(frozen=True)
class CharacterImageReviewResult:
    score: int | None
    goal: ImageGoalReview | None
    failure: ImageGoalReviewFailure | None = None


async def require_image_goal_review_config(user_id: int, task: AiTaskKey) -> UserLlmConfig:
    config = await load_user_llm_config(
        user_id,
        task,
        required_modalities=frozenset({ModelModality.text, ModelModality.image}),
    )
    if not config.is_configured:
        raise MissingLlmConfigError("图片画面检查需要支持图像输入的语言模型，请先完成配置")
    return config


async def review_character_image_goal(
    user_id: int,
    reference_uri: str,
    candidate_uri: str,
    *,
    generation_prompt: str,
    identity_reference_mode: Literal["primary", "portrait_and_outfit"],
    secondary_uri: str | None = None,
    environment_uri: str | None = None,
    identity_text: str = "",
    prepared_action_frame: bool = False,
    previous_failure: ImageGoalReviewFailure | None = None,
    task: AiTaskKey,
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> CharacterImageReviewResult:
    """按冻结身份检查候选；续审只补缺失内容，保留已有分数与结论。"""
    images = (
        reference_uri,
        *((secondary_uri,) if secondary_uri else ()),
        *((environment_uri,) if environment_uri else ()),
        candidate_uri,
    )
    if any(not image for image in images):
        logger.warning("character image goal review skipped: image missing", extra={"user_id": user_id})
        return CharacterImageReviewResult(None, None, ImageGoalReviewFailure(status="invalid"))
    if identity_reference_mode == "primary":
        reference_roles = "图 1 是完整身份参考，提供面容、物种、身体比例、身体结构和固有材质。"
        if secondary_uri:
            reference_roles += "图 2 只提供本次造型及携带物，不提供面容、身体结构或比例，也不参与身份评分。"
    else:
        reference_roles = "图 1 是头像身份参考，只提供面容、五官、脸型、物种与头部标志特征，不据此推断身材或服装。"
        if secondary_uri:
            reference_roles += (
                "图 2 提供身体比例、身体结构和固有材质，并提供本次造型及携带物；"
                "其面部细节不覆盖图 1，衣物轮廓不作为被遮挡身体的依据。"
            )
    if environment_uri:
        reference_roles += f"图 {len(images) - 1} 提供环境与光线，只核对环境，不参与角色身份评分。"
    diagnostics: list[LlmAttemptDiagnostic] = []
    original_seen = False
    original_score = previous_failure.score if previous_failure else None
    original_goal = previous_failure.verdict if previous_failure else None
    original_quality = previous_failure.quality_verdict if previous_failure else None

    def parse_output(value: object) -> _CharacterImageReview:
        nonlocal original_seen, original_score, original_goal, original_quality
        if not original_seen:
            original_seen = True
            # 无法解析的原文没有可保留的视觉决定，格式编辑不能补作判断。
            if not any(item.validation in ("invalid_json", "invalid_contract") for item in diagnostics) and isinstance(
                value,
                dict,
            ):
                score = value.get("score")
                if type(score) is int and 0 <= score <= 100 and original_score is None:
                    original_score = score
                goal = value.get("goal_verdict")
                if isinstance(goal, str) and goal in ("pass", "review", "fail") and original_goal is None:
                    original_goal = goal
                quality = value.get("quality_verdict")
                if isinstance(quality, str) and quality in ("pass", "review", "fail") and original_quality is None:
                    original_quality = quality
        if original_score is None or original_goal is None or original_quality is None:
            raise LlmJsonValidationError("missing_visual_decision", fields=("score", "goal_verdict", "quality_verdict"))
        result = _CharacterImageReview.model_validate(value)
        if (
            result.score != original_score
            or result.goal_verdict != original_goal
            or result.quality_verdict != original_quality
        ):
            raise LlmJsonValidationError("visual_decision_changed", fields=("score", "goal_verdict", "quality_verdict"))
        return result

    try:
        geometry = None
        if prepared_action_frame:
            candidate_data, _mime = await resolve_reference_bytes(candidate_uri)
            geometry = asdict(await run_blocking(measure_action_frame, candidate_data))
            diagnostic = await run_blocking(action_frame_review_input, candidate_data)
            images = (*images[:-1], build_data_uri(diagnostic, "image/png"))
        llm_config = await require_image_goal_review_config(user_id, task)
        result = await call_llm_json(
            llm_config,
            CHARACTER_IMAGE_GOAL_REVIEW.format(
                reference_roles=reference_roles,
                accept_score=MEDIA_IDENTITY_ACCEPT_SCORE,
            ),
            {
                "image_count": len(images),
                "identity": identity_text,
                "generation_prompt": generation_prompt,
                "image_geometry": geometry,
                "saved_decisions": {
                    "score": original_score,
                    "goal_verdict": original_goal,
                    "quality_verdict": original_quality,
                },
            },
            schema=_CharacterImageReview.model_json_schema(),
            schema_name="character_image_goal_review",
            parse_output=parse_output,
            repair_prompt=CHARACTER_IMAGE_GOAL_REVIEW_REPAIR,
            reference_images=images,
            preserve_choices=("goal_verdict", "quality_verdict"),
            before_call=before_submit,
            diagnostics=diagnostics,
        )
        logger.info(
            "character image reviewed",
            extra={
                "user_id": user_id,
                "score": result.score,
                "identity_reason": result.identity_reason[:200],
                "goal_verdict": result.goal_verdict,
                "goal_reason": result.goal_reason[:200],
                "quality_verdict": result.quality_verdict,
                "quality_reason": result.quality_reason[:200],
            },
        )
        checks = (
            ImageGoalReview(verdict=result.goal_verdict, reason=result.goal_reason),
            ImageGoalReview(verdict=result.quality_verdict, reason=result.quality_reason),
        )
        goal = max(checks, key=lambda check: {"pass": 0, "review": 1, "fail": 2}[check.verdict])
        goal.quality_verdict = result.quality_verdict
        return CharacterImageReviewResult(result.score, goal)
    except LlmCallBlockedError:
        raise
    except Exception as exc:
        logger.warning("character image goal review unavailable", extra={"user_id": user_id}, exc_info=True)
        response_seen = original_seen or any(item.response_status is not None for item in diagnostics)
        return CharacterImageReviewResult(
            original_score,
            None,
            ImageGoalReviewFailure(
                status="blocked"
                if classify_api_error(exc).reason == FailoverReason.content_policy_blocked
                else "invalid"
                if response_seen
                else "unavailable",
                score=original_score,
                verdict=original_goal,
                quality_verdict=original_quality,
            ),
        )


def _score_output_category(payload: object) -> str:
    """评分输出不合约的类别；日志只记类别与长度，原文只在 LLM 调试日志中。"""
    if not isinstance(payload, dict):
        return "non_json" if payload is None else "non_object"
    if "score" not in payload:
        return "missing_score"
    return "out_of_range" if type(payload["score"]) is int else "non_int"


async def _score_media(
    user_id: int,
    prompt: str,
    images: tuple[str, ...],
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
    *,
    task: AiTaskKey,
) -> int | None:
    if not images or any(not image for image in images):
        logger.warning("character media score skipped: image missing", extra={"user_id": user_id})
        return None
    try:
        raw = await vision_chat(
            user_id,
            prompt.format(accept_score=MEDIA_IDENTITY_ACCEPT_SCORE),
            json.dumps({"image_count": len(images), "identity": identity_text}, ensure_ascii=False),
            reference_images=images,
            before_submit=before_submit,
            task=task,
        )
        payload = parse_llm_json(raw)
        score = payload.get("score") if isinstance(payload, dict) else None
        if type(score) is int and 0 <= score <= 100:
            logger.info(
                "character media scored",
                extra={"user_id": user_id, "score": score, "reason": str(payload.get("reason") or "")[:200]},
            )
            return score
        logger.warning(
            "character media score output invalid",
            extra={"user_id": user_id, "category": _score_output_category(payload), "output_chars": len(raw)},
        )
    except LlmCallBlockedError:
        raise
    except Exception:
        logger.warning("character media score failed", extra={"user_id": user_id}, exc_info=True)
    return None


async def score_character_image(
    user_id: int,
    identity_uri: str,
    candidate_uri: str,
    *,
    task: AiTaskKey,
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> int | None:
    return await _score_media(
        user_id,
        CHARACTER_MEDIA_IMAGE_SCORE,
        (identity_uri, candidate_uri),
        identity_text,
        before_submit,
        task=task,
    )


async def score_character_image_with_references(
    user_id: int,
    identity_uris: tuple[str, ...],
    candidate_uri: str,
    *,
    task: AiTaskKey,
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> int | None:
    """按头像和当前完整着装图的职责共同评分图片候选。"""
    return await _score_media(
        user_id,
        CHARACTER_MEDIA_SELF_IMAGE_SCORE,
        (*identity_uris, candidate_uri),
        identity_text,
        before_submit,
        task=task,
    )


async def score_character_frames(
    user_id: int,
    identity_uri: str | None,
    frame_uris: tuple[str, ...],
    *,
    task: AiTaskKey,
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> int | None:
    return await _score_media(
        user_id,
        CHARACTER_MEDIA_VIDEO_SCORE,
        (identity_uri or "", *frame_uris),
        identity_text,
        before_submit,
        task=task,
    )


async def score_character_frames_with_references(
    user_id: int,
    identity_uris: tuple[str, ...],
    frame_uris: tuple[str, ...],
    prompt: str,
    *,
    task: AiTaskKey,
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> int | None:
    """按有序的多张身份参考及视频帧评分。"""
    return await _score_media(
        user_id,
        prompt,
        (*identity_uris, *frame_uris),
        identity_text,
        before_submit,
        task=task,
    )
