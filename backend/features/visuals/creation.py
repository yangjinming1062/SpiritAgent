"""影像活动的冻结计划、已有画面检查与有界修复状态。"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable

from infrastructure.ai.prompt_engineer import vision_chat_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import (
    build_data_uri,
    compute_file_sha256,
    read_valid_image_data_uri,
    resolve_asset_reference,
    validate_image_bytes,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from features.appearance.character_card import render_character_identity
from features.media.chain import MEDIA_IDENTITY_ACCEPT_SCORE
from features.media.identity_review import ImageGoalReviewFailure, review_character_image_goal

from .models import VisualJob
from .ports import read_planning_context
from .prompts import VISUAL_CREATION_PLAN, VISUAL_OPENING_REPAIR, visual_source_reference_prompt
from .repository import VisualStateError
from .schemas import ActorClipSource, CinematicSource, OpeningReview, VisualCreationPlan, VisualJobProgress


class VisualCreationError(VisualStateError):
    def __init__(self, message: str, failure_reason: str, *, repair_reason: str | None = None) -> None:
        super().__init__(message)
        self.failure_reason = failure_reason
        self.repair_reason = repair_reason


class _CreationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: bool = Field(strict=True)
    reason: str = Field(max_length=200)
    plan: VisualCreationPlan | None

    @model_validator(mode="after")
    def valid_plan(self) -> "_CreationDecision":
        if self.supported != (self.plan is not None):
            raise ValueError("支持活动时必须提供计划，不支持时计划须为空")
        return self


class _OpeningRepair(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opening_description: str = Field(min_length=1, max_length=1800)


async def ensure_creation_plan(
    user_id: int,
    job: VisualJob,
    snapshot: ActorClipSource | CinematicSource,
    progress: VisualJobProgress,
    *,
    before_submit: Callable[[], Awaitable[None]],
) -> None:
    if progress.creation_plan is not None:
        return
    opening = progress.pose_path or progress.previous_pose_path
    if opening:
        paths = (opening,)
        reference_roles = "图 1 提供实际人物、造型和环境"
    else:
        paths = (
            snapshot.primary_reference_path,
            snapshot.outfit.path,
            *((snapshot.scene.path,) if snapshot.kind == "cinematic" else ()),
        )
        reference_roles = visual_source_reference_prompt(
            snapshot.identity_reference_role, include_scene=snapshot.kind == "cinematic"
        )
    images = tuple(await asyncio.gather(*(asyncio.to_thread(read_valid_image_data_uri, path) for path in paths)))
    if not all(images):
        raise VisualStateError("活动参考画面无法读取，请重新准备素材")
    decision = await vision_chat_json(
        user_id,
        VISUAL_CREATION_PLAN,
        {
            "reference_mode": "opening" if opening else "sources",
            "reference_roles": reference_roles,
            "asset_kind": snapshot.kind,
            "personality": snapshot.persona.get("personality", ""),
            "companion_context": {}
            if progress.source == "base_preparation"
            else (await read_planning_context(user_id, snapshot)).model_dump(),
            "action": progress.action_description,
            "requirements": snapshot.requirements,
            "feedback": job.feedback,
            "video_feedback": job.video_feedback,
            "customization_priority": progress.customization_priority,
            "kind": snapshot.action.clip_kind,
            "duration_seconds": progress.duration_seconds,
        },
        response_model=_CreationDecision,
        schema_name="visual_creation_plan",
        preserve_choices=("supported",),
        reference_images=images,
        before_submit=before_submit,
        task=AiTaskKey.visuals,
    )
    if not decision.supported:
        raise VisualCreationError(
            decision.reason or "当前角色或环境不适合这个活动，请更换动作",
            "activity_unsupported",
        )
    progress.creation_plan = decision.plan
    progress.motion_prompt = ""


async def ensure_opening_repair(
    user_id: int,
    job: VisualJob,
    progress: VisualJobProgress,
    *,
    snapshot: ActorClipSource | CinematicSource,
    before_submit: Callable[[], Awaitable[None]],
) -> None:
    if not progress.repair_reason or progress.repair_description:
        return
    reference_path = progress.previous_pose_path or snapshot.primary_reference_path
    image = await asyncio.to_thread(read_valid_image_data_uri, reference_path)
    if not image:
        raise VisualStateError("修复参考画面无法读取")
    repair = await vision_chat_json(
        user_id,
        VISUAL_OPENING_REPAIR,
        {
            "creation_plan": progress.creation_plan.model_dump() if progress.creation_plan else None,
            "user_requirements": job.feedback,
            "video_feedback": job.video_feedback,
            "customization_priority": progress.customization_priority,
            "review_reason": progress.repair_reason,
            "reference_role": "opening" if progress.previous_pose_path else "identity",
        },
        response_model=_OpeningRepair,
        schema_name="visual_opening_repair",
        reference_images=(image,),
        before_submit=before_submit,
        task=AiTaskKey.visuals,
    )
    progress.repair_description = repair.opening_description


def opening_requirements(job: VisualJob, progress: VisualJobProgress) -> str:
    plan = progress.creation_plan
    if plan is None:
        raise VisualStateError("活动制作计划尚未准备好")
    return json.dumps(
        {
            "activity": plan.activity,
            "opening_description": plan.opening_description,
            "required_props": plan.required_props,
            "motion_scope": "静态图只需具备后续运动的起始条件，不要求同时呈现运动过程、结束状态或多个阶段",
            "user_requirements": job.feedback,
            "video_feedback": job.video_feedback,
            "customization_priority": progress.customization_priority,
            "customization_scope": "只核对静态可见要求和后续运动的起始条件；两类用户要求冲突时，customization_priority 指定的较新要求只覆盖明确涉及的维度，其余继续生效",
            "opening_adjustment": progress.repair_description,
            "opening_state": "单个可执行的活动开始状态，必要物品可见且可操作，姿态有合理支撑和接触关系",
        },
        ensure_ascii=False,
    )


def opening_image_hash(path: str) -> str:
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise VisualStateError("起始画面无法读取")
    return compute_file_sha256(resolved[0])


def opening_requirements_hash(job: VisualJob, progress: VisualJobProgress) -> str:
    return hashlib.sha256(opening_requirements(job, progress).encode()).hexdigest()


def opening_reference_hash(snapshot: ActorClipSource | CinematicSource) -> str:
    references = {
        "identity": snapshot.primary_reference_hash,
        "role": snapshot.identity_reference_role,
        "outfit": snapshot.outfit.content_hash,
        "scene": snapshot.scene.content_hash if snapshot.kind == "cinematic" else None,
        "identity_text": render_character_identity(snapshot.identity),
    }
    return hashlib.sha256(json.dumps(references, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_visual_reference(path: str | None, expected_hash: str | None) -> str:
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise VisualStateError("画面检查的参考图片无法读取，请重新准备素材")
    try:
        data = resolved[0].read_bytes()
        if expected_hash is not None and hashlib.sha256(data).hexdigest() != expected_hash:
            raise VisualStateError("画面检查的参考图片已变化，请重新准备制作")
        data, mime = validate_image_bytes(data)
    except (OSError, ValueError) as exc:
        raise VisualStateError("画面检查的参考图片无法读取，请重新准备素材") from exc
    return build_data_uri(data, mime)


async def review_existing_opening(
    user_id: int,
    job: VisualJob,
    progress: VisualJobProgress,
    *,
    snapshot: ActorClipSource | CinematicSource,
    before_submit: Callable[[], Awaitable[None]],
) -> OpeningReview:
    image_hash = await asyncio.to_thread(opening_image_hash, progress.pose_path)
    requirements_hash = opening_requirements_hash(job, progress)
    reference_hash = opening_reference_hash(snapshot)
    saved = progress.opening_review
    if saved is not None and (
        saved.image_hash != image_hash
        or saved.requirements_hash != requirements_hash
        or saved.reference_hash != reference_hash
    ):
        saved = None
    if saved is not None and saved.status == "complete" and saved.quality_verdict is not None:
        return saved
    image = await asyncio.to_thread(read_valid_image_data_uri, progress.pose_path)
    if not image:
        raise VisualStateError("起始画面无法读取")
    reference = await asyncio.to_thread(
        read_visual_reference,
        snapshot.primary_reference_path,
        snapshot.primary_reference_hash,
    )
    secondary = await asyncio.to_thread(read_visual_reference, snapshot.outfit.path, snapshot.outfit.content_hash)
    reference_mode = snapshot.identity_reference_mode
    result = await review_character_image_goal(
        user_id,
        reference,
        image,
        secondary_uri=secondary,
        generation_prompt=opening_requirements(job, progress),
        identity_reference_mode=reference_mode,
        identity_text=render_character_identity(snapshot.identity),
        previous_failure=ImageGoalReviewFailure(
            status="invalid" if saved.status == "complete" else saved.status,
            score=saved.score,
            verdict=saved.verdict,
            quality_verdict=saved.quality_verdict,
        )
        if saved is not None
        else None,
        environment_uri=await asyncio.to_thread(
            read_visual_reference,
            snapshot.scene.path,
            snapshot.scene.content_hash,
        )
        if snapshot.kind == "cinematic"
        else None,
        task=AiTaskKey.visuals,
        before_submit=before_submit,
    )
    # goal 与 failure 互斥且同名字段语义一致；失败时沿用已保存判定，续审只补缺失项。
    decided = result.goal or result.failure
    progress.opening_review = OpeningReview(
        image_hash=image_hash,
        requirements_hash=requirements_hash,
        reference_hash=reference_hash,
        status=result.failure.status if result.failure else "complete",
        score=result.score,
        verdict=decided.verdict if decided else None,
        quality_verdict=decided.quality_verdict if decided else None,
        reason=result.goal.reason if result.goal else "画面检查暂未完成",
    )
    return progress.opening_review


def opening_passed(review: OpeningReview) -> bool:
    return (
        review.status == "complete"
        and review.verdict == "pass"
        and review.quality_verdict == "pass"
        and review.score is not None
        and review.score >= MEDIA_IDENTITY_ACCEPT_SCORE
    )


def identity_score_passed(review: OpeningReview | None) -> bool:
    """身份分达标即可把候选作为修复底图，不要求画面要求与自然度同时通过。"""
    return review is not None and review.score is not None and review.score >= MEDIA_IDENTITY_ACCEPT_SCORE


def begin_opening_repair(
    job: VisualJob,
    progress: VisualJobProgress,
    *,
    reason: str,
    reference_path: str | None,
    new_budget: bool = False,
) -> None:
    if job.repair_count >= job.repair_limit and not new_budget:
        raise VisualCreationError(
            "这次还未准备好适合活动的画面，可以点击再试一次",
            "image_unsuitable",
            repair_reason=reason,
        )
    if new_budget:
        job.repair_count = 0
        progress.motion_replan_count = 0
    else:
        job.repair_count += 1
    progress.repair_reason = reason
    progress.repair_description = ""
    progress.previous_pose_path = reference_path
    progress.regenerate_image = not bool(reference_path)
    progress.pose_path = ""
    progress.image_chain_json = None
    progress.opening_review = None
    progress.image_confirmed = False
    progress.image_only = True
    progress.motion_prompt = ""
    progress.visual_description = ""
    progress.outfit_description = None
    progress.failure_reason = ""
