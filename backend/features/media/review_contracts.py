"""视觉判断、不可变检查依据和待复核状态的共享契约。"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ReviewVerdict = Literal["pass", "review", "fail"]
ReviewServiceStatus = Literal["complete", "unavailable", "invalid", "blocked"]


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: ReviewVerdict
    reason: str = Field(min_length=1, max_length=1000)


class ReviewImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str


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
    required: tuple[Literal["identity", "goal", "quality"], ...]
    identity: ReviewDecision | None = None
    goal: ReviewDecision | None = None
    quality: ReviewDecision | None = None
    status: ReviewServiceStatus = "complete"
    evidence: ReviewEvidence | None = None
    human_approval: HumanReviewApproval | None = None

    @property
    def verdict(self) -> ReviewVerdict:
        if (
            self.status == "complete"
            and self.human_approval is not None
            and self.evidence is not None
            and self.required
            and (
                self.human_approval.content_basis_id == self.evidence.content_basis_id
                and set(self.required).issubset(self.human_approval.dimensions)
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


def review_context(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ReviewPendingError(Exception):
    """已知媒体必须保留；调用方持久等待并退出执行。"""

    def __init__(
        self, message: str = "图片或视频已保留，等待选择候选", *, status: ReviewServiceStatus = "complete"
    ) -> None:
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
