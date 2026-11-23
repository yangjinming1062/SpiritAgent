"""视觉判断、不可变检查依据和待复核状态的共享契约。"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from infrastructure.ai.error_classifier import FailoverReason, LlmCallBlockedError, classify_api_error
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReviewVerdict = Literal["pass", "review", "fail"]
ReviewServiceStatus = Literal["complete", "unavailable", "invalid", "blocked"]


def review_failure_status(exc: BaseException, response_seen: bool = False) -> ReviewServiceStatus:
    if isinstance(exc, LlmCallBlockedError) or classify_api_error(exc).reason == FailoverReason.content_policy_blocked:
        return "blocked"
    return "invalid" if response_seen else "unavailable"


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: ReviewVerdict
    reason: str = Field(min_length=1, max_length=1000)


class OpeningStateReview(ReviewDecision):
    requirement: str = Field(max_length=240)
    correction_instruction: str = Field(max_length=400)


class FramedOpeningReview(OpeningStateReview):
    framing_requirement: str = Field(max_length=240, description="逐字摘取从开场生效的明确景别，未指定则填空串。")
    required_extent: Literal["whole", "partial", "free"] = Field(description="只读原文。须全身、须局部或未限定。")
    visible_extent: Literal["whole", "partial", "unclear"] = Field(
        description="只看真正的视频首帧。整个主体在画框内为 whole，画框截断主体为 partial，无法辨认填 unclear。"
    )


class ReviewImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str


class ObservedVideoHold(BaseModel):
    """原片中已经拍到的起始与到位姿势，供一次本地动作纠偏使用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    opening: ReviewImage
    resting: ReviewImage
    requirement: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=600)


class ReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_revision: int = Field(default=1, ge=1, strict=True)
    media_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_prompt: str = Field(min_length=1)
    policy_version: Literal[1] = 1
    schema_version: Literal[1] = 1
    context_json: str = Field(min_length=2)
    images: tuple[ReviewImage, ...] = Field(min_length=1)

    @property
    def image_labels(self) -> tuple[str, ...]:
        return tuple(image.role for image in self.images)

    @field_validator("context_json")
    @classmethod
    def valid_context(cls, value: str) -> str:
        if not isinstance(json.loads(value), dict):
            raise ValueError("检查上下文必须是对象")
        return value

    @property
    def basis_id(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @property
    def content_basis_id(self) -> str:
        value = self.model_dump(mode="json")
        value["images"] = [{"sha256": entry.sha256, "role": entry.role} for entry in self.images]
        return hashlib.sha256(review_context(value).encode()).hexdigest()


class HumanReviewApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    approved_at: datetime
    content_basis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: tuple[Literal["identity", "goal", "quality"], ...]


class VisualReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_basis: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    required: tuple[Literal["identity", "goal", "quality", "opening", "motion", "face"], ...]
    identity: ReviewDecision | None = None
    goal: ReviewDecision | None = None
    quality: ReviewDecision | None = None
    opening: FramedOpeningReview | OpeningStateReview | None = None
    motion: ReviewDecision | None = None
    face: ReviewDecision | None = None
    steps_before_stop: int | None = Field(default=None, ge=1, strict=True)
    step_requirement: str | None = Field(default=None, max_length=200)
    correction_instruction: str = Field(default="", max_length=400)
    opening_frame_instruction: str | None = Field(default=None, max_length=400)
    fixed_ambient_scene: bool = False
    status: ReviewServiceStatus = "complete"
    evidence: ReviewEvidence | None = None
    human_approval: HumanReviewApproval | None = None

    @property
    def face_check_passes(self) -> bool:
        return self.face.verdict == "pass" if self.face is not None else "face" not in self.required

    @property
    def verdict(self) -> ReviewVerdict:
        if (
            self.status == "complete"
            and self.human_approval is not None
            and self.evidence is not None
            and self.required
            and (
                self.human_approval.content_basis_id == self.evidence.content_basis_id
                and self.face_check_passes
                and set(self.required).difference({"face"}).issubset(self.human_approval.dimensions)
            )
        ):
            return "pass"
        return self.model_verdict

    @property
    def model_verdict(self) -> ReviewVerdict:
        decisions = [getattr(self, name) for name in self.required]
        if any(item is not None and item.verdict == "fail" for item in decisions):
            return "fail"
        if self.status != "complete" or self.evidence is None or not self.required:
            return "review"
        return "pass" if all(item is not None and item.verdict == "pass" for item in decisions) else "review"

    def correction_notes(self) -> str:
        if self.status != "complete" or not self.evidence or self.human_approval or not self.required:
            return ""
        notes: dict[str, str] = {}
        for name in self.required:
            decision = getattr(self, name)
            if decision is None:
                return ""
            if decision.verdict != "pass":
                notes[name] = decision.reason[:240]
        return review_context(notes) if notes else ""


class MediaSelection(BaseModel):
    """一次性交付决定绑定原始检查内容；不会改变资产入库资格。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    basis_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mode: Literal["passed", "best_available", "not_delivered"]
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_basis(self) -> "MediaSelection":
        if (self.mode == "not_delivered") != (self.basis_id is None):
            raise ValueError("选中版本必须绑定该候选的检查依据")
        return self

    def permits(self, review: VisualReview | None, required: tuple[str, ...]) -> bool:
        if (
            self.mode == "not_delivered"
            or review is None
            or review.evidence is None
            or review.status != "complete"
            or self.basis_id != review.evidence.content_basis_id
            or not set(required).issubset(review.required)
            or any(getattr(review, name) is None for name in required)
            or review.human_approval is not None
            or (review.motion is not None and review.motion.verdict != "pass")
            or (review.opening is not None and review.opening.verdict != "pass")
            or not review.face_check_passes
        ):
            return False
        if "identity" in required and review.identity.verdict != "pass":
            return False
        if "face" in review.required:
            return self.mode == "passed" and review.model_verdict == "pass"
        return self.mode == "best_available" or review.model_verdict == "pass"


def optional_quality_choice(review: VisualReview | None) -> bool:
    """仅质量存疑可在手动制作详情选择。身份、用途及检查服务均须明确合格。"""
    return bool(
        review
        and review.evidence
        and review.status == "complete"
        and review.verdict == "review"
        and {"goal", "quality"}.issubset(review.required)
        and review.quality
        and review.quality.verdict == "review"
        and all(
            getattr(review, name) and getattr(review, name).verdict == "pass"
            for name in review.required
            if name != "quality"
        )
    )


def review_context(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ReviewPendingError(Exception):
    """已知媒体必须保留；调用方持久等待并退出执行。"""

    def __init__(self, message: str = "媒体尚未通过必要检查", *, status: ReviewServiceStatus = "complete") -> None:
        super().__init__(message)
        self.status = status


def candidate_review_key(generation_id: str, slot: int = 0) -> str:
    return hashlib.sha256(review_context({"generation_id": generation_id, "slot": slot}).encode()).hexdigest()


def visual_reference_basis(source: dict) -> str:
    action = source.get("action") or {}
    action = {key: value for key, value in action.items() if key not in {"action_id", "name", "revision"}}
    value = {
        "identity": source.get("identity_hash"),
        "portrait": source.get("portrait_hash"),
        "role": source.get("identity_reference_role"),
        "outfit": (source.get("outfit") or {}).get("content_hash"),
        "scene": (source.get("scene") or {}).get("content_hash"),
        "action": action,
        "requirements": source.get("requirements", ""),
    }
    if opening_frame := source.get("opening_frame"):
        value["opening_frame"] = opening_frame["content_hash"]
    return hashlib.sha256(review_context(value).encode()).hexdigest()


def merge_review_evidence(identity: ReviewEvidence | None, quality: ReviewEvidence | None) -> ReviewEvidence | None:
    if identity is None or quality is None:
        return quality or identity
    if identity.media_hash != quality.media_hash:
        raise ValueError("不同候选的检查依据不能合并")
    prompt = review_context({"identity": identity.policy_prompt, "goal_quality": quality.policy_prompt})
    return ReviewEvidence(
        input_revision=max(identity.input_revision, quality.input_revision),
        media_hash=quality.media_hash,
        policy_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        policy_prompt=prompt,
        context_json=review_context(
            {"identity": json.loads(identity.context_json), "goal_quality": json.loads(quality.context_json)}
        ),
        images=tuple(entry.model_copy(update={"role": f"identity.{entry.role}"}) for entry in identity.images)
        + tuple(entry.model_copy(update={"role": f"goal_quality.{entry.role}"}) for entry in quality.images),
    )


def needs_review(review: VisualReview | None) -> bool:
    if review is not None and review.human_approval is not None and review.verdict == "pass":
        return False
    return (
        review is None
        or review.status in {"unavailable", "invalid"}
        or review.status == "complete"
        and review.verdict == "review"
    )


def review_passes(review: VisualReview | None, required: tuple[str, ...]) -> bool:
    return review is not None and set(required).issubset(review.required) and review.verdict == "pass"


def review_dimension_passes(review: VisualReview | None, name: str) -> bool:
    if review is None:
        return False
    if review.human_approval is not None and review.verdict == "pass" and name in review.human_approval.dimensions:
        return True
    decision = getattr(review, name)
    return decision is not None and decision.verdict == "pass"


def review_basis(
    review: VisualReview | None, evidence: ReviewEvidence | None, fallback: str | None = None
) -> str | None:
    """优先取本次判断的证据；尚无判断时用已冻结候选证据，最后才回退到候选身份键。"""

    if review is not None and review.evidence is not None:
        return review.evidence.basis_id
    if evidence is not None:
        return evidence.basis_id
    return fallback
