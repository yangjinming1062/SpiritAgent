"""桌面生活视频的公共契约与冻结资料。"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.constants import POSTGRES_INT4_MAX
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from features.action_library.schemas import ActionVariantSummary
from features.media.identity_contracts import CharacterCardSnapshot, CharacterReferenceRole
from features.media.video_queue.contracts import VideoQueueInfo

DESKTOP_MAX_EDGE = 65536


class DesktopVisualSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: CharacterCardSnapshot
    identity_path: str = Field(min_length=1)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    portrait_path: str = Field(min_length=1)
    portrait_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_reference_role: CharacterReferenceRole = "portrait"
    outfit_id: int | None = None
    outfit_path: str = Field(min_length=1)
    outfit_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outfit_description: str = ""
    scene_id: int
    scene_description: str
    scene_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_path: str = Field(min_length=1)
    persona: dict[str, str] = Field(default_factory=dict)

    @property
    def primary_reference_path(self) -> str:
        return self.identity_path if self.identity_reference_role == "fullbody" else self.portrait_path


def desktop_visual_hash(snapshot: DesktopVisualSnapshot) -> str:
    visual = {
        "avatar_id": snapshot.identity.avatar_id,
        "identity_hash": snapshot.identity_hash,
        "portrait_hash": snapshot.portrait_hash,
        "outfit_id": snapshot.outfit_id,
        "outfit_hash": snapshot.outfit_hash,
        "outfit_description": snapshot.outfit_description,
        "scene_id": snapshot.scene_id,
        "scene_description": snapshot.scene_description,
        "scene_hash": snapshot.scene_hash,
        "spec_version": 2,
    }
    if snapshot.identity_reference_role == "fullbody":
        visual["identity_reference_role"] = snapshot.identity_reference_role
    return hashlib.sha256(json.dumps(visual, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DesktopCreationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    activity: str = Field(min_length=1, max_length=600)
    opening_description: str = Field(min_length=1, max_length=1800)
    required_props: list[str] = Field(default_factory=list, max_length=20)
    motion_requirements: str = Field(min_length=1, max_length=1800)


class DesktopOpeningReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["complete", "unavailable", "invalid"] = "complete"
    score: int | None = Field(default=None, ge=0, le=100, strict=True)
    verdict: Literal["pass", "review", "fail"] | None = None
    reason: str = ""


class DesktopVideoAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    image_path: str = Field(min_length=1)
    source_image_path: str | None = None
    source_video_path: str | None = None
    geometry_mode: Literal["target", "keep"]
    resolution_revision: int = Field(gt=0, strict=True)
    image_origin: GenerationOrigin | None = None
    video_origin: GenerationOrigin | None = None
    source: Literal["generated", "external_upload"] = "generated"
    transition: Literal["base", "fade"] = "fade"
    visual_description: str = Field(default="", max_length=1500)
    creation_plan: DesktopCreationPlan | None = None
    feedback: str = ""
    video_feedback: str = ""
    customization_priority: Literal["general", "video"] = "general"
    poster_path: str
    duration_ms: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    continuity: Literal["independent", "base"] = "independent"
    base_version: int | None = Field(default=None, gt=0, strict=True)
    base_image_path: str | None = None

    @model_validator(mode="after")
    def valid_continuity(self) -> "DesktopVideoAsset":
        if self.continuity == "base":
            if self.base_version is None or not self.base_image_path or not self.base_image_path.strip():
                raise ValueError("相接视频须保留基础画面及其版本")
        elif self.base_version is not None or self.base_image_path is not None:
            raise ValueError("独立视频不能声明基础画面")
        return self


class DesktopVideoRefinementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str = Field(min_length=1)
    original_video_path: str = Field(min_length=1)
    image_path: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    video_origin: GenerationOrigin | None = None


class DesktopVideoProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_id: str
    target_width: int = Field(gt=0, le=DESKTOP_MAX_EDGE, strict=True)
    target_height: int = Field(gt=0, le=DESKTOP_MAX_EDGE, strict=True)
    resolution_revision: int = Field(gt=0, strict=True)
    geometry_mode: Literal["target", "keep"] = "target"
    source_image_path: str | None = None
    expand_image: bool = False
    source: Literal["user_requested", "autonomous", "external_upload"]
    feedback: str = ""
    video_feedback: str = ""
    refinement_feedback: str | None = None
    customization_priority: Literal["general", "video"] = "general"
    paused_by_user: bool = False
    image_confirmed: bool = False
    image_only: bool = False
    auto_continue: bool = False
    aspect_ratio: str = Field(default="16:9", pattern=r"^[1-9]\d*:[1-9]\d*$")
    frame_width: int | None = Field(default=None, gt=0)
    frame_height: int | None = Field(default=None, gt=0)
    previous_pose_path: str | None = None
    previous_image_origin: GenerationOrigin | None = None
    image_origin: GenerationOrigin | None = None
    image_selection: ProviderSelection | None = None
    video_selection: ProviderSelection | None = None
    regenerate_image: bool = False
    force_review: bool = False
    visual_description: str = Field(default="", max_length=1500)
    action_description: str = ""
    creation_plan: DesktopCreationPlan | None = None
    opening_review: DesktopOpeningReview | None = None
    repair_count: int = Field(default=0, ge=0, le=1, strict=True)
    repair_reason: str = ""
    repair_description: str = ""
    previous_image_chain_json: str | None = None
    previous_opening_review: DesktopOpeningReview | None = None
    motion_replan_count: int = Field(default=0, ge=0, le=1, strict=True)
    duration_seconds: int
    continuity: Literal["independent", "base"] = "independent"
    base_version: int | None = Field(default=None, gt=0, strict=True)
    base_image_path: str | None = None
    replace_base: bool = False
    motion_prompt: str = ""
    pose_path: str = ""
    reference_alignment_chain_json: str | None = None
    reference_alignment_source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    aligned_outfit_path: str | None = Field(default=None, min_length=1)
    aligned_outfit_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    image_chain_json: str | None = None
    video_chain_json: str | None = None
    provider_task_id: str | None = None
    download_url: str | None = None
    source_path: str | None = None
    reprocess_only: bool = False
    video_refinement: DesktopVideoRefinementInput | None = None
    candidate: DesktopVideoAsset | None = None
    identity_score: int | None = None
    review_verdict: Literal["pass", "review"] | None = None
    review_reason: str = ""
    submission_unknown: bool = False
    failure_reason: str = ""

    @property
    def video_adjustment(self) -> str:
        return (
            self.refinement_feedback if self.refinement_feedback is not None else self.video_feedback or self.feedback
        )

    @model_validator(mode="after")
    def valid_aligned_reference(self) -> "DesktopVideoProgress":
        if bool(self.aligned_outfit_path) != bool(self.aligned_outfit_hash):
            raise ValueError("桌景派生造型参考须同时保留路径与内容哈希")
        if (
            self.reference_alignment_chain_json or self.aligned_outfit_path
        ) and not self.reference_alignment_source_hash:
            raise ValueError("桌景参考对齐缺少冻结来源")
        return self

    @model_validator(mode="after")
    def valid_continuity(self) -> "DesktopVideoProgress":
        if self.continuity == "base":
            if self.base_version is None:
                raise ValueError("相接制作须冻结基础画面版本")
            if self.base_image_path is not None and not self.base_image_path.strip():
                raise ValueError("基础画面引用不能为空")
        elif self.base_version is not None or self.base_image_path is not None or self.replace_base:
            raise ValueError("独立制作不能声明或更新基础画面")
        return self


class DesktopVideoCurrentResponse(BaseModel):
    video_url: str
    poster_url: str
    image_url: str = ""
    feedback: str = ""
    video_feedback: str = ""
    duration_ms: int
    width: int
    height: int
    geometry_mode: Literal["target", "keep"]
    continuity: Literal["independent", "base"]
    base_version: int | None = None
    transition: Literal["base", "fade"] = "fade"


class DesktopVideoDraftResponse(BaseModel):
    id: str
    stage: str
    source: Literal["user_requested", "autonomous", "external_upload"]
    target_width: int
    target_height: int
    resolution_revision: int
    geometry_mode: Literal["target", "keep"] = "target"
    duration_seconds: int
    video_refinement: bool = False
    frame_width: int | None = None
    frame_height: int | None = None
    feedback: str = ""
    video_feedback: str = ""
    submission_unknown: bool = False
    image_url: str = ""
    image_confirmed: bool = False
    continuity: Literal["independent", "base"] = "independent"
    recovery_action: Literal["retry", "wait", "choose_activity", "replace_upload"] | None = None
    candidate_video_url: str = ""
    candidate_poster_url: str = ""
    review_reason: str = ""
    base_version: int | None = None
    error: str | None = None
    video_queue: VideoQueueInfo | None = None


class DesktopVideoActionResponse(BaseModel):
    id: int
    set_id: int
    definition_id: int | None = None
    sync_targets: list[ActionVariantSummary] = Field(default_factory=list)
    can_generate: bool = False
    current: DesktopVideoCurrentResponse | None = None
    draft: DesktopVideoDraftResponse | None = None
    playable: bool = False
    unavailable_reason: str | None = None
    needs_update: bool = False
    key: str
    name: str
    description: str
    kind: Literal["loop", "once"]
    duration_seconds: int
    target_duration_seconds: int
    status: Literal["not_generated", "queued", "processing", "ready", "failed", "review_pending", "blocked"]
    stage: str
    error: str | None = None
    video_queue: VideoQueueInfo | None = None
    enabled: bool
    preset: bool
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    allowed_triggers: list[Literal["periodic", "user_return", "chat", "manual"]] = Field(default_factory=list)
    continuity: Literal["independent", "base"] = "independent"
    transition: Literal["base", "fade"] = "fade"
    base_version: int | None = Field(default=None, gt=0, strict=True)
    poster_url: str = ""
    video_url: str = ""
    version: int
    actual_duration_ms: int | None = None


class DesktopVideoProposalResponse(BaseModel):
    proposal_id: int
    set_id: int
    action_id: int | None = None
    definition_id: int | None = None
    sync_targets: list[ActionVariantSummary] = Field(default_factory=list)
    status: str
    review_reason: str = ""
    name: str = ""
    description: str = ""
    created_at: datetime | None = None


class DesktopVideoSetResponse(BaseModel):
    id: int
    title: str
    target_width: int
    target_height: int
    resolution_revision: int
    is_default: bool = False
    source: Literal["ai", "image_upload", "video_upload"] = "ai"
    outfit_id: int | None = None
    scene_id: int | None = None
    status: str
    actions: list[DesktopVideoActionResponse] = Field(default_factory=list)
    proposals: list[DesktopVideoProposalResponse] = Field(default_factory=list)
    created_at: datetime
    is_current: bool = False
    base_image_url: str = ""
    base_version: int = Field(default=0, ge=0, strict=True)


class DesktopVideoStateResponse(BaseModel):
    current: DesktopVideoSetResponse | None = None
    selected_action_id: int | None = None
    loop_action_id: int | None = None
    set_epoch: int = 0
    pinned: bool = False
    autonomous_enabled: bool = True
    version: int = 0
    preparation_error: str | None = None


class DesktopVideoListResponse(BaseModel):
    sets: list[DesktopVideoSetResponse] = Field(default_factory=list)
    version: int = 0


class DesktopVideoGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str | None = Field(default=None, max_length=1000)
    video_feedback: str | None = Field(default=None, max_length=1000)
    expected_draft_id: str | None = None
    expected_base_version: int | None = Field(default=None, ge=0)
    expected_resolution_revision: int | None = Field(default=None, gt=0)
    aspect_policy: Literal["ask", "keep", "expand"] = "ask"

    @field_validator("expected_draft_id", mode="before")
    @classmethod
    def explicit_no_draft(cls, value: str | None) -> str:
        return value or "none"


class DesktopVideoRefineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["current", "candidate"]
    feedback: str = Field(default="", max_length=600)
    aspect_policy: Literal["keep", "expand"] = "keep"
    expected_action_version: int = Field(ge=0)
    expected_draft_id: str | None = None
    expected_base_version: int = Field(ge=0)
    expected_resolution_revision: int = Field(gt=0)

    @field_validator("expected_draft_id", mode="before")
    @classmethod
    def explicit_no_draft(cls, value: str | None) -> str:
        return value or "none"


class DesktopVideoRefinePreviewResponse(BaseModel):
    duration_seconds: int
    width: int
    height: int


class DesktopVideoCreateSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00]*$")
    name: str = Field(min_length=1, max_length=160, pattern=r"^[^\x00]*$")
    scene_id: int = Field(gt=0, le=POSTGRES_INT4_MAX)
    outfit_id: int = Field(gt=0, le=POSTGRES_INT4_MAX)
    source: Literal["ai", "image_upload", "video_upload"] = "ai"
    feedback: str = Field(default="", max_length=1000)
    target_width: int = Field(gt=0, le=DESKTOP_MAX_EDGE, strict=True)
    target_height: int = Field(gt=0, le=DESKTOP_MAX_EDGE, strict=True)

    @field_validator("name", "request_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称和请求标识不能为空")
        return value.strip()


class DesktopVideoRenameSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160, pattern=r"^[^\x00]*$")

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("套装名称不能为空")
        return value.strip()


class DesktopVideoImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str | None = Field(default=None, max_length=1000)
    regenerate: bool = False
    expected_draft_id: str | None = None
    expected_base_version: int | None = Field(default=None, ge=0)
    expected_resolution_revision: int | None = Field(default=None, gt=0)
    aspect_policy: Literal["ask", "keep", "expand"] = "ask"

    @field_validator("expected_draft_id", mode="before")
    @classmethod
    def explicit_no_draft(cls, value: str | None) -> str:
        return value or "none"


class DesktopVideoDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    expected_base_version: int | None = Field(default=None, ge=0)
    expected_resolution_revision: int | None = Field(default=None, gt=0)


class DesktopVideoConfirmImageRequest(DesktopVideoDraftRequest):
    generate_video: bool = True


class DesktopVideoCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str | None = None


class DesktopVideoExternalPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_set_id: int | None = Field(default=None, gt=0, le=POSTGRES_INT4_MAX)
    requirements: str = Field(default="", max_length=1000)
    expected_draft_id: str | None = None
    expected_base_version: int | None = Field(default=None, ge=0)
    expected_resolution_revision: int | None = Field(default=None, gt=0)
    aspect_policy: Literal["ask", "keep"] = "ask"

    @field_validator("expected_draft_id", mode="before")
    @classmethod
    def explicit_no_draft(cls, value: str | None) -> str:
        return value or "none"


class DesktopVideoReference(BaseModel):
    label: str
    url: str


class DesktopVideoExternalPromptResponse(BaseModel):
    draft_id: str | None = None
    base_version: int = 0
    action_id: int
    set_id: int
    name: str
    description: str
    kind: Literal["loop", "once"]
    duration_seconds: int
    prompt: str
    references: list[DesktopVideoReference] = Field(default_factory=list)


class DesktopVideoEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: Literal["entry"] = "entry"
    target_width: int = Field(gt=0, le=DESKTOP_MAX_EDGE, strict=True)
    target_height: int = Field(gt=0, le=DESKTOP_MAX_EDGE, strict=True)


class DesktopVideoPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pinned: bool | None = None
    autonomous_enabled: bool | None = None


class DesktopVideoDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00]*$")
    motion_description: str = Field(min_length=10, max_length=600, pattern=r"^[^\x00]*$")
    kind: Literal["loop", "once"] = "once"
    continuity: Literal["independent", "base"] = "independent"
    allowed_triggers: list[Literal["periodic", "user_return", "chat", "manual"]] = Field(
        default_factory=lambda: ["chat", "manual"],
        min_length=1,
        max_length=4,
    )
    duration_seconds: int = Field(default=6, ge=1, le=15, strict=True)
    use_when: list[str] = Field(default_factory=list, max_length=8)
    avoid_when: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=400)
    expected_set_id: int | None = Field(default=None, gt=0, le=POSTGRES_INT4_MAX)

    @field_validator("name", "motion_description")
    @classmethod
    def nonblank(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError("动作名称和描述不能为空")
        if info.field_name == "motion_description" and len(value.strip()) < 10:
            raise ValueError("动作描述至少需要10个字符")
        return value.strip()

    @field_validator("use_when", "avoid_when")
    @classmethod
    def bounded_conditions(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 120 for item in value):
            raise ValueError("动作条件须为不超过120字的非空文字")
        return value

    @field_validator("allowed_triggers")
    @classmethod
    def unique_triggers(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("允许触发情境不能重复")
        return value


class DesktopVideoPlayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: int = Field(gt=0, le=POSTGRES_INT4_MAX)
    expected_set_id: int | None = Field(default=None, gt=0, le=POSTGRES_INT4_MAX)
    reason: str = Field(default="", max_length=400)


class DesktopVideoPlayCommand(BaseModel):
    play_id: str
    set_id: int
    action_id: int
    set_epoch: int
    kind: Literal["loop", "once"]
    expires_at: datetime
    video_url: str
    poster_url: str
    geometry_mode: Literal["target", "keep"]
    version: int
    transition: Literal["base", "fade"] = "fade"
    continuity: Literal["independent", "base"]
    base_version: int | None = Field(default=None, gt=0, strict=True)


class DesktopVideoClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class DesktopVideoReceipt(DesktopVideoClaimRequest):
    status: Literal["started", "completed", "interrupted", "failed", "rejected"]
    error: str | None = Field(default=None, max_length=500, pattern=r"^[^\x00]*$")


class DesktopVideoGenerateCommand(DesktopVideoGenerateRequest):
    command: Literal["generate-video"]


class DesktopVideoImageCommand(DesktopVideoImageRequest):
    command: Literal["generate-image"]


class DesktopVideoRefineCommand(DesktopVideoRefineRequest):
    command: Literal["refine-video"]


class DesktopVideoConfirmImageCommand(DesktopVideoConfirmImageRequest):
    command: Literal["confirm-image"]


class DesktopVideoCancelCommand(DesktopVideoCancelRequest):
    command: Literal["cancel"]


class DesktopVideoPauseCommand(DesktopVideoDraftRequest):
    command: Literal["pause"]


class DesktopVideoResumeCommand(DesktopVideoDraftRequest):
    command: Literal["resume"]


class DesktopVideoRetryCommand(DesktopVideoDraftRequest):
    command: Literal["retry"]


class DesktopVideoDecisionRequest(DesktopVideoDraftRequest):
    decision: Literal["accept", "reject"]


class DesktopVideoSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_id: int = Field(gt=0, le=POSTGRES_INT4_MAX)
