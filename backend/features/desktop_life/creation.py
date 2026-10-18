"""桌面活动的冻结计划、已有画面检查与有界修复状态。"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Literal

from infrastructure.ai.prompt_engineer import vision_chat
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import (
    build_data_uri,
    compute_file_sha256,
    read_valid_image_data_uri,
    resolve_asset_reference,
)
from infrastructure.text import parse_llm_json
from pydantic import BaseModel, ConfigDict, Field

from features.desktop_life.prompts import DESKTOP_CREATION_PLAN, DESKTOP_OPENING_REPAIR, desktop_source_reference_prompt
from features.desktop_life.repository import DesktopVideoStateError
from features.desktop_life.schemas import (
    DesktopCreationPlan,
    DesktopOpeningReview,
    DesktopVideoProgress,
    DesktopVisualSnapshot,
)
from features.media.chain import MEDIA_IDENTITY_ACCEPT_SCORE
from features.media.character_images import CharacterImageInput, ImageChainState, image_asset_bytes
from features.media.identity_review import review_character_image_goal


class DesktopCreationError(DesktopVideoStateError):
    def __init__(self, message: str, failure_reason: str, *, repair_reason: str | None = None) -> None:
        super().__init__(message)
        self.failure_reason = failure_reason
        self.repair_reason = repair_reason


class _CreationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: bool = Field(strict=True)
    reason: str = Field(max_length=200)
    plan: DesktopCreationPlan | None


class _OpeningRepair(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opening_description: str = Field(min_length=1, max_length=1800)


async def ensure_creation_plan(
    user_id: int,
    snapshot: DesktopVisualSnapshot,
    progress: DesktopVideoProgress,
    *,
    kind: str,
    before_submit: Callable[[], Awaitable[None]],
) -> None:
    if progress.creation_plan is not None:
        return
    opening = (
        progress.pose_path
        or progress.previous_pose_path
        or (progress.base_image_path if not progress.replace_base else None)
    )
    paths = (opening,) if opening else (snapshot.primary_reference_path, snapshot.outfit_path, snapshot.scene_path)
    images = tuple(await asyncio.gather(*(asyncio.to_thread(read_valid_image_data_uri, path) for path in paths)))
    if not all(images):
        raise DesktopVideoStateError("活动参考画面无法读取，请重新准备素材")
    raw = await vision_chat(
        user_id,
        DESKTOP_CREATION_PLAN,
        json.dumps(
            {
                "reference_mode": "opening" if opening else "sources",
                "reference_roles": desktop_source_reference_prompt(
                    snapshot.identity_reference_role,
                    include_scene=True,
                ),
                "persona": snapshot.persona,
                "environment": snapshot.scene_description,
                "action": progress.action_description,
                "feedback": progress.feedback,
                "video_feedback": progress.video_feedback,
                "customization_priority": progress.customization_priority,
                "kind": kind,
                "duration_seconds": progress.duration_seconds,
                "continuity": progress.continuity,
            },
            ensure_ascii=False,
        ),
        reference_images=images,
        before_submit=before_submit,
        task=AiTaskKey.desktop_life,
    )
    decision = _CreationDecision.model_validate(parse_llm_json(raw))
    if not decision.supported:
        raise DesktopCreationError(
            decision.reason or "当前角色或环境不适合这个活动，请选择其他活动",
            "activity_unsupported",
        )
    if decision.plan is None:
        raise DesktopCreationError("活动计划暂未准备好，可以重试", "planning_unavailable")
    progress.creation_plan = decision.plan
    progress.motion_prompt = ""


async def ensure_opening_repair(
    user_id: int,
    progress: DesktopVideoProgress,
    *,
    snapshot: DesktopVisualSnapshot,
    before_submit: Callable[[], Awaitable[None]],
) -> None:
    if not progress.repair_reason or progress.repair_description:
        return
    reference_path = progress.previous_pose_path or snapshot.primary_reference_path
    image = await asyncio.to_thread(read_valid_image_data_uri, reference_path)
    if not image:
        raise DesktopVideoStateError("修复参考画面无法读取")
    raw = await vision_chat(
        user_id,
        DESKTOP_OPENING_REPAIR,
        json.dumps(
            {
                "creation_plan": progress.creation_plan.model_dump() if progress.creation_plan else None,
                "user_requirements": progress.feedback,
                "video_feedback": progress.video_feedback,
                "customization_priority": progress.customization_priority,
                "review_reason": progress.repair_reason,
                "reference_role": "opening" if progress.previous_pose_path else "identity",
            },
            ensure_ascii=False,
        ),
        reference_images=(image,),
        before_submit=before_submit,
        task=AiTaskKey.desktop_life,
    )
    progress.repair_description = _OpeningRepair.model_validate(parse_llm_json(raw)).opening_description


def opening_requirements(progress: DesktopVideoProgress) -> str:
    plan = progress.creation_plan
    if plan is None:
        raise DesktopVideoStateError("活动制作计划尚未准备好")
    return json.dumps(
        {
            "activity": plan.activity,
            "opening_description": plan.opening_description,
            "required_props": plan.required_props,
            "motion_requirements": plan.motion_requirements,
            "motion_scope": "静态图只需具备后续运动的起始条件，不要求同时呈现运动过程、结束状态或多个阶段",
            "user_requirements": progress.feedback,
            "video_feedback": progress.video_feedback,
            "customization_priority": progress.customization_priority,
            "customization_scope": "只核对静态可见要求和后续运动的起始条件；两类用户要求冲突时，customization_priority 指定的较新要求只覆盖明确涉及的维度，其余继续生效",
            "reference_authority": "已确认及上传图片自身提供身份与造型，不相容的旧文字不能覆盖它；未确认生成候选须对照冻结参考，不能用候选自身证明身份正确",
            "opening_adjustment": progress.repair_description,
            "opening_state": "单个可执行的活动开始状态，必要物品可见且可操作，姿态有合理支撑和接触关系",
        },
        ensure_ascii=False,
    )


def opening_image_hash(path: str) -> str:
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise DesktopVideoStateError("起始画面无法读取")
    return compute_file_sha256(resolved[0])


def opening_requirements_hash(progress: DesktopVideoProgress) -> str:
    return hashlib.sha256(opening_requirements(progress).encode()).hexdigest()


async def review_existing_opening(
    user_id: int,
    progress: DesktopVideoProgress,
    *,
    before_submit: Callable[[], Awaitable[None]],
) -> DesktopOpeningReview:
    image_hash = await asyncio.to_thread(opening_image_hash, progress.pose_path)
    requirements_hash = opening_requirements_hash(progress)
    saved = progress.opening_review
    if (
        saved is not None
        and saved.image_hash == image_hash
        and saved.requirements_hash == requirements_hash
        and saved.status != "unavailable"
    ):
        return saved
    image = await asyncio.to_thread(read_valid_image_data_uri, progress.pose_path)
    if not image:
        raise DesktopVideoStateError("起始画面无法读取")
    reference, secondary = image, None
    reference_mode: Literal["primary", "portrait_and_outfit"] = "primary"
    # 未确认生成图仍须对照冻结来源；已确认及上传画面自身拥有身份和造型。
    if not progress.image_confirmed and progress.image_chain_json:
        chain = ImageChainState.model_validate_json(progress.image_chain_json)
        inputs = chain.inputs
        if isinstance(inputs, CharacterImageInput):
            candidate = chain.best()
            if candidate is not None and candidate.score is not None and candidate.score < MEDIA_IDENTITY_ACCEPT_SCORE:
                progress.opening_review = DesktopOpeningReview(
                    image_hash=image_hash,
                    requirements_hash=requirements_hash,
                    score=candidate.score,
                    verdict="fail",
                    reason="这张生成图片未通过原身份检查，需要按冻结身份重新制作",
                )
                return progress.opening_review
            data, mime = await image_asset_bytes(inputs.identity_reference)
            reference = build_data_uri(data, mime)
            reference_mode = inputs.identity_reference_mode
            if inputs.secondary_reference_image:
                data, mime = await image_asset_bytes(inputs.secondary_reference_image)
                secondary = build_data_uri(data, mime)
    result = await review_character_image_goal(
        user_id,
        reference,
        image,
        secondary_uri=secondary,
        generation_prompt=opening_requirements(progress),
        identity_reference_mode=reference_mode,
        task=AiTaskKey.desktop_life,
        before_submit=before_submit,
    )
    progress.opening_review = DesktopOpeningReview(
        image_hash=image_hash,
        requirements_hash=requirements_hash,
        status=result.failure.status if result.failure else "complete",
        score=result.score,
        verdict=result.goal.verdict if result.goal else result.failure.verdict if result.failure else None,
        reason=result.goal.reason if result.goal else "画面检查暂未完成",
    )
    return progress.opening_review


def opening_passed(review: DesktopOpeningReview) -> bool:
    return (
        review.status == "complete"
        and review.verdict == "pass"
        and review.score is not None
        and review.score >= MEDIA_IDENTITY_ACCEPT_SCORE
    )


def begin_opening_repair(
    progress: DesktopVideoProgress,
    *,
    reason: str,
    reference_path: str | None,
    separate_base: bool = False,
    new_budget: bool = False,
    replace_base: bool = False,
) -> None:
    if progress.repair_count >= 1 and not separate_base and not new_budget:
        raise DesktopCreationError(
            "这次还未准备好适合活动的画面，可以点击再试一次",
            "image_unsuitable",
            repair_reason=reason,
        )
    if new_budget:
        progress.repair_count = 0
        progress.motion_replan_count = 0
    elif not separate_base:
        progress.repair_count += 1
    if separate_base:
        progress.continuity = "independent"
        progress.base_version = None
        progress.base_image_path = None
        progress.replace_base = False
    elif replace_base and progress.continuity == "base" and not progress.replace_base:
        progress.replace_base = True
        progress.base_version = (progress.base_version or 0) + 1
    progress.repair_reason = reason
    progress.repair_description = ""
    if progress.image_chain_json:
        progress.previous_image_chain_json = progress.image_chain_json
    if progress.opening_review:
        progress.previous_opening_review = progress.opening_review
    progress.previous_pose_path = reference_path
    progress.regenerate_image = not bool(reference_path)
    progress.pose_path = ""
    progress.image_chain_json = None
    progress.opening_review = None
    progress.image_confirmed = False
    progress.image_only = True
    progress.motion_prompt = ""
    progress.visual_description = ""
    progress.failure_reason = ""
