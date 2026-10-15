"""桌面生活视频的公共契约与冻结资料。"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from features.media.identity_contracts import CharacterCardSnapshot


class DesktopVisualSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: CharacterCardSnapshot
    identity_path: str = Field(min_length=1)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    portrait_path: str = Field(min_length=1)
    portrait_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outfit_id: int | None = None
    outfit_path: str = Field(min_length=1)
    outfit_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outfit_description: str = ""
    scene_id: int
    scene_description: str
    scene_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_path: str = Field(min_length=1)
    persona: dict[str, str] = Field(default_factory=dict)


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
    return hashlib.sha256(json.dumps(visual, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DesktopVideoAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    image_path: str = Field(min_length=1)
    source: Literal["generated", "external_upload"] = "generated"
    transition: Literal["base", "fade"] = "fade"
    visual_description: str = Field(default="", max_length=1500)
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


class DesktopVideoProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_id: str
    source: Literal["user_requested", "autonomous", "external_upload"]
    feedback: str = ""
    image_confirmed: bool = False
    image_only: bool = False
    aspect_ratio: str = Field(default="16:9", pattern=r"^[1-9]\d*:[1-9]\d*$")
    frame_width: int | None = Field(default=None, gt=0)
    frame_height: int | None = Field(default=None, gt=0)
    previous_pose_path: str | None = None
    regenerate_image: bool = False
    force_review: bool = False
    paid_slot_consumed: bool = False
    visual_description: str = Field(default="", max_length=1500)
    action_description: str = ""
    duration_seconds: int
    continuity: Literal["independent", "base"] = "independent"
    base_version: int | None = Field(default=None, gt=0, strict=True)
    base_image_path: str | None = None
    replace_base: bool = False
    motion_prompt: str = ""
    pose_path: str = ""
    image_chain_json: str | None = None
    video_chain_json: str | None = None
    provider_task_id: str | None = None
    download_url: str | None = None
    source_path: str | None = None
    candidate: DesktopVideoAsset | None = None
    identity_score: int | None = None
    review_verdict: Literal["pass", "review"] | None = None
    review_reason: str = ""
    submission_unknown: bool = False
    failure_reason: str = ""

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
    duration_ms: int
    width: int
    height: int
    continuity: Literal["independent", "base"]
    base_version: int | None = None
    transition: Literal["base", "fade"] = "fade"


class DesktopVideoDraftResponse(BaseModel):
    id: str
    stage: str
    image_url: str = ""
    image_confirmed: bool = False
    candidate_video_url: str = ""
    candidate_poster_url: str = ""
    base_version: int | None = None
    error: str | None = None


class DesktopVideoActionResponse(BaseModel):
    id: int
    set_id: int
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
    status: Literal["queued", "processing", "ready", "failed", "review_pending"]
    stage: str
    error: str | None = None
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
    status: str
    review_reason: str = ""
    name: str = ""
    description: str = ""
    created_at: datetime | None = None


class DesktopVideoSetResponse(BaseModel):
    id: int
    title: str
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
    feedback: str = Field(default="", max_length=600)
    expected_draft_id: str | None = None
    expected_base_version: int | None = Field(default=None, ge=0)

    @field_validator("expected_draft_id", mode="before")
    @classmethod
    def explicit_no_draft(cls, value: str | None) -> str:
        return value or "none"


class DesktopVideoCreateSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    scene_id: int = Field(gt=0)
    outfit_id: int = Field(gt=0)
    source: Literal["ai", "image_upload", "video_upload"] = "ai"

    @field_validator("name", "request_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称和请求标识不能为空")
        return value.strip()


class DesktopVideoRenameSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("套装名称不能为空")
        return value.strip()


class DesktopVideoImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str = Field(default="", max_length=1000)
    regenerate: bool = False
    expected_draft_id: str | None = None
    expected_base_version: int | None = Field(default=None, ge=0)

    @field_validator("expected_draft_id", mode="before")
    @classmethod
    def explicit_no_draft(cls, value: str | None) -> str:
        return value or "none"


class DesktopVideoDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    expected_base_version: int | None = Field(default=None, ge=0)


class DesktopVideoConfirmImageRequest(DesktopVideoDraftRequest):
    generate_video: bool = True


class DesktopVideoCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str | None = None


class DesktopVideoExternalPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_set_id: int | None = Field(default=None, gt=0)
    requirements: str = Field(default="", max_length=1000)
    expected_draft_id: str | None = None
    expected_base_version: int | None = Field(default=None, ge=0)

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


class DesktopVideoPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pinned: bool | None = None
    autonomous_enabled: bool | None = None


class DesktopVideoDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)
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
    expected_set_id: int | None = Field(default=None, gt=0)

    @field_validator("name", "motion_description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("动作名称和描述不能为空")
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
    action_id: int = Field(gt=0)
    expected_set_id: int | None = Field(default=None, gt=0)
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
    version: int
    transition: Literal["base", "fade"] = "fade"
    continuity: Literal["independent", "base"]
    base_version: int | None = Field(default=None, gt=0, strict=True)


class DesktopVideoClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class DesktopVideoReceipt(DesktopVideoClaimRequest):
    status: Literal["started", "completed", "interrupted", "failed", "rejected"]
    error: str | None = Field(default=None, max_length=500)
