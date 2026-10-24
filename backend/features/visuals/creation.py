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
from infrastructure.assets.paths import parse_companion_asset_path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from features.media.chain import MEDIA_IDENTITY_ACCEPT_SCORE
from features.media.character_images import CharacterImageInput, ImageChainState, image_asset_bytes
from features.media.identity_review import review_character_image_goal

from .models import VisualJob
from .ports import read_planning_context
from .prompts import VISUAL_CREATION_PLAN, VISUAL_OPENING_REPAIR, visual_source_reference_prompt
from .repository import VisualStateError
from .schemas import ComboSnapshot, OpeningReview, VisualCreationPlan, VisualJobProgress


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
    snapshot: ComboSnapshot,
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
        environment = ""
    else:
        paths = (snapshot.primary_reference_path, snapshot.outfit.path, snapshot.scene.path)
        reference_roles = visual_source_reference_prompt(snapshot.identity_reference_role, include_scene=True)
        environment = snapshot.scene.description
    images = tuple(await asyncio.gather(*(asyncio.to_thread(read_valid_image_data_uri, path) for path in paths)))
    if not all(images):
        raise VisualStateError("活动参考画面无法读取，请重新准备素材")
    decision = await vision_chat_json(
        user_id,
        VISUAL_CREATION_PLAN,
        {
            "reference_mode": "opening" if opening else "sources",
            "reference_roles": reference_roles,
            "persona": snapshot.persona,
            "companion_context": (await read_planning_context(user_id, snapshot)).model_dump(),
            "environment": environment,
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
    snapshot: ComboSnapshot,
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
            "motion_requirements": plan.motion_requirements,
            "motion_scope": "静态图只需具备后续运动的起始条件，不要求同时呈现运动过程、结束状态或多个阶段",
            "user_requirements": job.feedback,
            "video_feedback": job.video_feedback,
            "customization_priority": progress.customization_priority,
            "customization_scope": "只核对静态可见要求和后续运动的起始条件；两类用户要求冲突时，customization_priority 指定的较新要求只覆盖明确涉及的维度，其余继续生效",
            "reference_authority": "已确认图片自身提供人物外观与穿着，不相容的旧文字不能覆盖它；新生成但尚未确认的图片须对照原有参考，不能用待检查图片自身证明人物外观正确",
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


def _read_opening_review_source(path: str | None, expected_hash: str | None) -> str:
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


async def _opening_review_reference(
    value: str | None,
    *,
    inputs: CharacterImageInput,
    progress: VisualJobProgress,
    snapshot: ComboSnapshot,
    secondary: bool = False,
) -> str:
    if value and (
        parse_companion_asset_path(value) is None or await asyncio.to_thread(resolve_asset_reference, value) is not None
    ):
        data, mime = await image_asset_bytes(value)
        return await asyncio.to_thread(build_data_uri, data, mime)
    # 链参考缺失时沿本次输入恢复；编辑和扩图必须保留原画面，不能改用头像。
    expected_hash: str | None = None
    if secondary:
        if progress.aligned_outfit_path:
            alignment = ImageChainState.model_validate_json(progress.reference_alignment_chain_json or "{}")
            selected = alignment.best()
            if (
                alignment.phase != "complete"
                or selected is None
                or selected.path != progress.aligned_outfit_path
                or not progress.aligned_outfit_hash
            ):
                raise VisualStateError("派生造型参考缺少已通过的对齐结果")
            path, expected_hash = progress.aligned_outfit_path, progress.aligned_outfit_hash
        else:
            path, expected_hash = snapshot.outfit.path, snapshot.outfit.content_hash
    elif inputs.expansion is not None:
        path = progress.previous_pose_path or progress.source_image_path
    elif inputs.image_edit:
        path = progress.previous_pose_path
    else:
        path = snapshot.primary_reference_path
        expected_hash = (
            snapshot.identity_hash if snapshot.identity_reference_role == "fullbody" else snapshot.portrait_hash
        )
    if not path:
        raise VisualStateError("画面检查的参考图片无法读取，请重新准备素材")
    reference = await asyncio.to_thread(_read_opening_review_source, path, expected_hash)
    if secondary:
        inputs.secondary_reference_image = path
    else:
        if inputs.reference_image == inputs.identity_reference:
            inputs.reference_image = path
        inputs.identity_reference = path
    return reference


async def review_existing_opening(
    user_id: int,
    job: VisualJob,
    progress: VisualJobProgress,
    *,
    snapshot: ComboSnapshot,
    before_submit: Callable[[], Awaitable[None]],
) -> OpeningReview:
    image_hash = await asyncio.to_thread(opening_image_hash, progress.pose_path)
    requirements_hash = opening_requirements_hash(job, progress)
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
        raise VisualStateError("起始画面无法读取")
    reference, secondary = image, None
    reference_mode = "primary"
    # 未确认生成图仍须对照冻结来源；已确认画面自身拥有身份和造型。
    if not progress.image_confirmed and progress.image_chain_json:
        chain = ImageChainState.model_validate_json(progress.image_chain_json)
        inputs = chain.inputs
        if isinstance(inputs, CharacterImageInput):
            candidate = chain.best()
            if candidate is not None and candidate.score is not None and candidate.score < MEDIA_IDENTITY_ACCEPT_SCORE:
                progress.opening_review = OpeningReview(
                    image_hash=image_hash,
                    requirements_hash=requirements_hash,
                    score=candidate.score,
                    verdict="fail",
                    reason="这张图片的人物外观与原有参考不一致，应依照原有参考重新制作",
                )
                return progress.opening_review
            reference = await _opening_review_reference(
                inputs.identity_reference,
                inputs=inputs,
                progress=progress,
                snapshot=snapshot,
            )
            reference_mode = inputs.identity_reference_mode
            if inputs.secondary_reference_image:
                secondary = await _opening_review_reference(
                    inputs.secondary_reference_image,
                    inputs=inputs,
                    progress=progress,
                    snapshot=snapshot,
                    secondary=True,
                )
            progress.image_chain_json = chain.model_dump_json()
    result = await review_character_image_goal(
        user_id,
        reference,
        image,
        secondary_uri=secondary,
        generation_prompt=opening_requirements(job, progress),
        identity_reference_mode=reference_mode,
        environment_uri=await asyncio.to_thread(
            _read_opening_review_source,
            snapshot.scene.path,
            snapshot.scene.content_hash,
        ),
        task=AiTaskKey.visuals,
        before_submit=before_submit,
    )
    progress.opening_review = OpeningReview(
        image_hash=image_hash,
        requirements_hash=requirements_hash,
        status=result.failure.status if result.failure else "complete",
        score=result.score,
        verdict=result.goal.verdict if result.goal else result.failure.verdict if result.failure else None,
        reason=result.goal.reason if result.goal else "画面检查暂未完成",
    )
    return progress.opening_review


def opening_passed(review: OpeningReview) -> bool:
    return (
        review.status == "complete"
        and review.verdict == "pass"
        and review.score is not None
        and review.score >= MEDIA_IDENTITY_ACCEPT_SCORE
    )


def opening_repair_reference(
    progress: VisualJobProgress,
    state: ImageChainState | None,
    identity_safe_path: str | None,
) -> str | None:
    if identity_safe_path:
        return identity_safe_path
    inputs = state.inputs if state is not None else None
    if not isinstance(inputs, CharacterImageInput) or not inputs.image_edit:
        return None
    for path in (
        inputs.identity_reference,
        progress.previous_pose_path or (progress.source_image_path if inputs.expansion is not None else None),
    ):
        if path and resolve_asset_reference(path) is not None:
            return path
    raise VisualStateError("画面修复的原始参考无法读取，请重新准备素材")


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
