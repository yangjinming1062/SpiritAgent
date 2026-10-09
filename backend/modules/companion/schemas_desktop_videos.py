"""桌面生活视频的公共契约与冻结资料。"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .character_card import CharacterCardSnapshot


class DesktopVisualSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: CharacterCardSnapshot
    identity_path: str
    identity_hash: str
    outfit_id: int | None = None
    outfit_path: str = ""
    outfit_hash: str = ""
    outfit_description: str = ""
    scene_id: int
    scene_description: str
    scene_hash: str = ""
    persona: dict[str, str] = Field(default_factory=dict)


def desktop_visual_hash(snapshot: DesktopVisualSnapshot) -> str:
    visual = {
        "avatar_id": snapshot.identity.avatar_id,
        "identity_hash": snapshot.identity_hash,
        "outfit_id": snapshot.outfit_id,
        "outfit_hash": snapshot.outfit_hash,
        "outfit_description": snapshot.outfit_description,
        "scene_id": snapshot.scene_id,
        "scene_description": snapshot.scene_description,
        "scene_hash": snapshot.scene_hash,
        "aspect_ratio": "16:9",
        "spec_version": 1,
    }
    return hashlib.sha256(json.dumps(visual, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DesktopVideoAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: str
    poster_path: str
    duration_ms: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class DesktopVideoProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_id: str
    source: Literal["user_requested", "autonomous"]
    feedback: str = ""
    duration_seconds: int
    pose_prompt: str = ""
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


class DesktopVideoActionResponse(BaseModel):
    id: int
    set_id: int
    key: str
    name: str
    description: str
    kind: Literal["loop", "once"]
    duration_seconds: int
    status: Literal["queued", "processing", "ready", "failed", "review_pending"]
    stage: str
    error: str | None = None
    enabled: bool
    preset: bool
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    poster_url: str = ""
    video_url: str = ""
    candidate_video_url: str = ""
    candidate_poster_url: str = ""
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
    outfit_id: int | None = None
    scene_id: int | None = None
    context_hash: str
    status: str
    actions: list[DesktopVideoActionResponse] = Field(default_factory=list)
    proposals: list[DesktopVideoProposalResponse] = Field(default_factory=list)
    created_at: datetime
    is_current: bool = False


class DesktopVideoStateResponse(BaseModel):
    current: DesktopVideoSetResponse | None = None
    fallback: DesktopVideoSetResponse | None = None
    desired_context_hash: str | None = None
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


class DesktopVideoEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: Literal["entry", "context_change"] = "entry"


class DesktopVideoPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pinned: bool | None = None
    autonomous_enabled: bool | None = None


class DesktopVideoDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)
    kind: Literal["loop", "once"] = "once"
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


class DesktopVideoClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class DesktopVideoReceipt(DesktopVideoClaimRequest):
    status: Literal["started", "completed", "interrupted", "failed", "rejected"]
    error: str | None = Field(default=None, max_length=500)
