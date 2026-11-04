"""媒体检查查询和显式续审的公开契约。"""

from typing import Literal

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .review_contracts import VisualReview


class ImageJobParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str
    request_scope: str
    prompt: str
    size: str
    subject: Literal["self"] | None = None
    reference_image: str | None
    secondary_reference_image: str | None
    identity_json: str | None
    identity_reference_mode: Literal["primary", "portrait_and_outfit"]
    task: AiTaskKey
    directory: str
    media_ids: list[str] = Field(min_length=1, max_length=16)
    goal_ids: list[str] = Field(min_length=1, max_length=16)
    parent_media_id: str | None = None

    @model_validator(mode="after")
    def valid_slots(self) -> "ImageJobParams":
        if len(self.media_ids) != len(self.goal_ids) or len(set(self.media_ids)) != len(self.media_ids):
            raise ValueError("图片任务槽位与原目标必须一一对应")
        if self.parent_media_id is not None and len(self.media_ids) != 1:
            raise ValueError("重做必须绑定单个原目标")
        return self

    @property
    def identity_required(self) -> bool:
        return self.subject == "self" or self.identity_json is not None

    @property
    def required_dimensions(self) -> tuple[str, ...]:
        return ("identity", "goal", "quality") if self.identity_required else ("goal", "quality")


class MediaReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    slot: int = Field(default=0, ge=0)
    expected_basis_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)


class MediaContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["save_outfit", "publish_post", "deliver_message"]
    expected_revision: int = Field(ge=0)


class MediaManualRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    slot: int = Field(default=0, ge=0)
    expected_revision: int = Field(ge=0)
    action: Literal["approve", "regenerate"]
    candidate_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class MediaCandidateView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    preview_url: str
    media_type: Literal["image", "video"]
    review: VisualReview | None = None
    can_approve: bool = False


class MediaSlotView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: int
    media_id: str
    label: str = ""
    status: str
    media_revision: int = 0
    url: str | None = None
    preview_url: str | None = None
    review: VisualReview | None = None
    basis_id: str | None = None
    candidate_basis_id: str | None = None
    can_review: bool = False
    error: str | None = None
    candidates: list[MediaCandidateView] = Field(default_factory=list)
    can_regenerate: bool = False
    delivered: bool = False


class MediaJobView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: ResourceId
    kind: str
    state: str
    stage: str
    revision: int
    slots: list[MediaSlotView]
    required_slots: int
    ready_slots: int
    complete: bool
    continuation_actions: list[Literal["save_outfit", "publish_post", "deliver_message"]] = Field(default_factory=list)
    image_request: ImageJobParams | None = None
    user_id: ResourceId | None = None
    session_id: str | None = None
    source_message_id: ResourceId | None = None
    bindings: dict[str, str] = Field(default_factory=dict)


def review_request_matches(view: MediaJobView, request: MediaReviewRequest) -> bool:
    return view.revision == request.expected_revision and any(
        slot.slot == request.slot and slot.basis_id == request.expected_basis_id and slot.can_review
        for slot in view.slots
    )
