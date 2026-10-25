"""影像库的组合快照、制作进度与对外 DTO。"""

import hashlib
from datetime import datetime
from typing import Literal

from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field

from features.media.identity_contracts import CharacterCardSnapshot, CharacterReferenceRole
from features.media.video_queue.contracts import VideoQueueInfo

VISUAL_MAX_EDGE = 65536

VisualMediaKind = Literal["image", "video"]
ActionClipKind = Literal["once", "loop"]
VisualJobState = Literal["queued", "running", "paused", "succeeded", "failed", "cancelled", "unknown"]
PlaybackStatus = Literal["queued", "claimed", "started", "finished", "failed", "expired"]
PlaybackReceiptKind = Literal["image", "video", "window_static"]
PlaybackSource = Literal["manual", "autonomous", "restore", "remote"]


class ComboMaterialRef(BaseModel):
    """衣柜或环境素材的冻结引用；素材删除后 `material_id` 置空。"""

    model_config = ConfigDict(extra="forbid")

    material_id: ResourceId | None = None
    name: str = ""
    path: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    description: str = ""
    revision: datetime | None = None


class ActionMaterialRef(BaseModel):
    """动作素材的冻结引用与语义；定义修改不追溯已生成影像。"""

    model_config = ConfigDict(extra="forbid")

    action_id: ResourceId | None = None
    name: str = Field(min_length=1)
    motion_description: str = Field(min_length=1)
    clip_kind: ActionClipKind
    duration_seconds: int = Field(ge=1, le=60, strict=True)
    use_when: list[str] = Field(default_factory=list, max_length=8)
    avoid_when: list[str] = Field(default_factory=list, max_length=8)
    allowed_triggers: list[str] = Field(default_factory=list, max_length=8)
    revision: datetime | None = None


class ComboSnapshot(BaseModel):
    """一次组合制作的完整冻结输入：身份、三份素材、计划、目标画幅与生成要求。"""

    model_config = ConfigDict(extra="forbid")

    identity: CharacterCardSnapshot
    identity_path: str = Field(min_length=1)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    portrait_path: str = Field(min_length=1)
    portrait_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_reference_role: CharacterReferenceRole = "portrait"
    outfit: ComboMaterialRef
    scene: ComboMaterialRef
    action: ActionMaterialRef
    plan: "VisualCreationPlan | None" = None
    target_width: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)
    target_height: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)
    requirements: str = ""
    feedback: str = ""
    video_feedback: str = ""
    persona: dict[str, str] = Field(default_factory=dict)

    @property
    def primary_reference_path(self) -> str:
        return self.identity_path if self.identity_reference_role == "fullbody" else self.portrait_path


class VisualCreationPlan(BaseModel):
    """具体活动计划：活动、起始画面、必要道具与运动要求。"""

    model_config = ConfigDict(extra="forbid")

    activity: str = Field(min_length=1, max_length=600)
    opening_description: str = Field(min_length=1, max_length=1800)
    required_props: list[str] = Field(default_factory=list, max_length=20)
    motion_requirements: str = Field(min_length=1, max_length=1800)


class OpeningReview(BaseModel):
    """起始画面的检查结论，绑定具体图片与要求哈希。"""

    model_config = ConfigDict(extra="forbid")

    image_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["complete", "unavailable", "invalid"] = "complete"
    score: int | None = Field(default=None, ge=0, le=100, strict=True)
    verdict: Literal["pass", "review", "fail"] | None = None
    reason: str = ""


class VisualAsset(BaseModel):
    """一份已通过检查的组合成品及其媒体事实。"""

    model_config = ConfigDict(extra="forbid")

    media_kind: VisualMediaKind
    media_path: str = Field(min_length=1)
    poster_path: str = ""
    input_image_path: str | None = Field(default=None, min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    image_origin: GenerationOrigin | None = None
    video_origin: GenerationOrigin | None = None
    visual_description: str = Field(default="", max_length=1500)
    outfit_description: str = Field(default="", max_length=1200)
    creation_plan: VisualCreationPlan | None = None
    feedback: str = ""
    video_feedback: str = ""


class VisualJobProgress(BaseModel):
    """制作工作项的持久化进度；恢复沿冻结输入继续，不重置预算。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1

    generation_id: str
    target_width: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)
    target_height: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)
    source: Literal["user_requested", "autonomous"]
    # 通用要求与视频要求分别保存；最近一次修改的一方在冲突维度上优先。
    customization_priority: Literal["general", "video"] = "general"
    image_only: bool = False
    paused_by_user: bool = False
    image_confirmed: bool = False
    aspect_ratio: str = Field(default="16:9", pattern=r"^[1-9]\d*:[1-9]\d*$")
    duration_seconds: int = Field(gt=0)
    regenerate_image: bool = False
    frame_width: int | None = Field(default=None, gt=0)
    frame_height: int | None = Field(default=None, gt=0)
    previous_pose_path: str | None = None
    image_origin: GenerationOrigin | None = None
    image_selection: ProviderSelection | None = None
    video_selection: ProviderSelection | None = None
    visual_description: str = Field(default="", max_length=1500)
    outfit_description: str | None = Field(default=None, max_length=1200)
    action_description: str = ""
    creation_plan: VisualCreationPlan | None = None
    opening_review: OpeningReview | None = None
    repair_reason: str = ""
    repair_description: str = ""
    motion_replan_count: int = Field(default=0, ge=0, le=1, strict=True)
    motion_prompt: str = ""
    pose_path: str = ""
    reference_alignment_chain_json: str | None = None
    reference_alignment_source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    aligned_outfit_path: str | None = Field(default=None, min_length=1)
    aligned_outfit_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    image_chain_json: str | None = None
    video_chain_json: str | None = None
    candidate: VisualAsset | None = None
    identity_score: int | None = None
    review_verdict: Literal["pass", "review"] | None = None
    review_reason: str = ""
    submission_unknown: bool = False
    failure_reason: str = ""
    source_path: str | None = None
    video_queue: VideoQueueInfo | None = None


def combo_snapshot_hash(snapshot: ComboSnapshot) -> str:
    """组合快照的内容哈希，用于校验冻结输入未被改写。"""

    return hashlib.sha256(snapshot.model_dump_json().encode("utf-8")).hexdigest()


def parse_job_progress(value: dict | None) -> VisualJobProgress | None:
    if not value:
        return None
    return VisualJobProgress.model_validate(value)


class VisualItemView(BaseModel):
    """影像成品对外视图；签名 URL 只在响应出口生成。"""

    model_config = ConfigDict(extra="forbid")

    id: ResourceId
    media_kind: VisualMediaKind
    clip_kind: ActionClipKind = "loop"
    title: str
    status: str
    media_url: str
    poster_url: str | None = None
    width: int
    height: int
    duration_ms: int | None = None
    version: int
    description: str = ""
    outfit_id: ResourceId | None = None
    outfit_name: str = ""
    scene_id: ResourceId | None = None
    scene_title: str = ""
    action_id: ResourceId | None = None
    action_name: str | None = None
    input_image_id: ResourceId | None = None
    input_image_hash: str | None = None
    created_at: datetime
    updated_at: datetime


class VisualJobView(BaseModel):
    """制作工作项对外视图。"""

    model_config = ConfigDict(extra="forbid")

    id: ResourceId
    purpose: str
    target_kind: VisualMediaKind
    target_visual_id: ResourceId | None = None
    expected_version: int | None = None
    state: VisualJobState
    stage: str
    feedback: str = ""
    video_feedback: str = ""
    repair_count: int = 0
    repair_limit: int = 1
    error: str | None = None
    result_visual_id: ResourceId | None = None
    result_image_id: ResourceId | None = None
    video_queue: VideoQueueInfo | None = None
    created_at: datetime
    updated_at: datetime


class VisualDetailView(VisualItemView):
    """成品详情：完整预览、组合来源与实际媒体信息。"""

    snapshot: ComboSnapshot
    replaced_at_version: int | None = None
    jobs: list[VisualJobView] = Field(default_factory=list)


class VisualPresentationView(BaseModel):
    """呈现状态与偏好：当前影像、持续画面和自主开关。"""

    model_config = ConfigDict(extra="forbid")

    current_visual: VisualItemView | None = None
    loop_visual: VisualItemView | None = None
    autonomous_enabled: bool = True
    target_width: int | None = None
    target_height: int | None = None
    presentation_epoch: int = 0
    state_revision: int = 0
    pending_play: "VisualPlayCommand | None" = None


class VisualPlayCommand(BaseModel):
    """派发给桌面客户端的呈现指令；`queued` 只表示受理。"""

    model_config = ConfigDict(extra="forbid")

    play_id: str
    status: Literal["queued"] = "queued"
    visual_id: ResourceId
    visual_version: int
    media_kind: VisualMediaKind
    media_path: str
    poster_path: str | None = None
    input_image_hash: str | None = None
    clip_kind: ActionClipKind
    loop: bool
    transition: Literal["natural", "fade"] = "fade"
    presentation_epoch: int
    state_revision: int
    source: PlaybackSource
    expires_at: datetime
    outfit: ComboMaterialRef
    scene: ComboMaterialRef


class PresentedVisual(BaseModel):
    """播放时冻结的成品版本；重做采用不改变已显示画面的事实或文件引用。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: ResourceId
    user_id: ResourceId
    media_kind: VisualMediaKind
    title: str
    status: str
    media_path: str
    poster_path: str | None
    width: int
    height: int
    duration_ms: int | None
    content_hash: str
    description: str
    outfit_description: str = ""
    snapshot_json: dict
    version: int
    outfit_id: ResourceId | None
    scene_id: ResourceId | None
    action_id: ResourceId | None
    input_image_version_id: ResourceId | None = None
    input_image_id: ResourceId | None
    input_image_path: str | None
    input_image_hash: str | None
    created_at: datetime
    updated_at: datetime
    play_id: str | None = None
    receipt_kind: PlaybackReceiptKind | None = None


class VisualRegenerateRequest(BaseModel):
    """重做所选影像：默认沿用冻结组合与已保存要求。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    feedback: str | None = Field(default=None, max_length=2000)
    video_feedback: str | None = Field(default=None, max_length=2000)


class ActualPresentationSnapshot(BaseModel):
    """实际呈现快照：窗口读取静态造型与环境资料，桌面与出镜媒体读取成品画面描述。

    不能把视频里的道具、姿态或动作当作窗口已经显示的事实；素材缺失时对应字段为空。
    """

    model_config = ConfigDict(extra="forbid")

    visual_id: ResourceId | None = None
    media_kind: VisualMediaKind | None = None
    visual_version: int | None = None
    visual_description: str = ""
    visual_path: str | None = None
    outfit_path: str | None = None
    outfit_hash: str | None = None
    outfit_description: str = ""
    scene_path: str | None = None
    scene_hash: str | None = None
    scene_description: str = ""
    identity_path: str | None = None
    identity_hash: str | None = None
    identity_reference_role: CharacterReferenceRole = "portrait"
    ready: bool = False


class VisualPresentationResponse(VisualPresentationView):
    """呈现状态响应：呈现字段平铺，另附实际呈现快照供窗口与出镜读取。"""

    model_config = ConfigDict(extra="forbid")

    actual: ActualPresentationSnapshot


class VisualPlayOption(BaseModel):
    """可播影像的轻量投影；visual_id 是本轮清单内可直接点播的成品。"""

    model_config = ConfigDict(extra="forbid")

    visual_id: ResourceId
    name: str
    media_kind: VisualMediaKind
    clip_kind: ActionClipKind
    duration_seconds: int
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)


class VisualListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visuals: list[VisualItemView]
    total: int | None = None
    has_more: bool
    next_cursor: str | None = None
    limit: int


class VisualJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[VisualJobView]
    total: int | None = None
    has_more: bool
    next_cursor: str | None = None
    limit: int


class VisualJobCommandRequest(BaseModel):
    """工作项命令：暂停、继续、取消和显式重试。"""

    model_config = ConfigDict(extra="forbid")

    command: Literal["pause", "resume", "cancel", "retry"]


class VisualClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=80)


class VisualReceiptRequest(BaseModel):
    """实际执行回执；静态投影不能记为视频动作已表演。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["started", "finished", "failed", "interrupted"]
    client_id: str = Field(min_length=1, max_length=80)
    receipt_kind: PlaybackReceiptKind | None = None
    error: str | None = Field(default=None, max_length=500)


class VisualPreferencesRequest(BaseModel):
    """呈现偏好与本机呈现上下文的目标画幅。"""

    model_config = ConfigDict(extra="forbid")

    autonomous_enabled: bool | None = None
    target_width: int | None = Field(default=None, gt=0, le=VISUAL_MAX_EDGE, strict=True)
    target_height: int | None = Field(default=None, gt=0, le=VISUAL_MAX_EDGE, strict=True)


class VisualClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claimed: bool


class VisualReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
