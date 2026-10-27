"""影像库的资产快照、舞台契约、制作进度与对外 DTO。

三类可播放资产共用一份成品身份与不可变版本结构，差别在冻结输入的判别联合：
`actor`（透明主体动画）绑定造型与动作、不绑定环境；`scene`（环境媒体）独立于
伙伴与动作；`cinematic`（完整场景影像）冻结身份、造型、环境与动作的整体组合。
"""

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from infrastructure.ai.selection import GenerationOrigin, ProviderSelection
from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from features.media.identity_contracts import CharacterCardSnapshot, CharacterReferenceRole
from features.media.video_queue.contracts import VideoQueueInfo

VISUAL_MAX_EDGE = 65536

VisualMediaKind = Literal["image", "video"]
VisualAssetKind = Literal["actor", "scene", "cinematic"]
ActionClipKind = Literal["once", "loop"]
VisualJobState = Literal["queued", "running", "paused", "succeeded", "failed", "cancelled", "unknown"]
PlaybackStatus = Literal["queued", "claimed", "started", "finished", "failed", "expired"]
PlaybackReceiptKind = Literal["image", "video", "window_static"]
PlaybackSource = Literal["manual", "autonomous", "restore", "remote"]
StageMode = Literal["layered", "cinematic"]


class ComboMaterialRef(BaseModel):
    """衣柜素材的冻结引用；素材删除后 `material_id` 置空。"""

    model_config = ConfigDict(extra="forbid")

    material_id: ResourceId | None = None
    name: str = ""
    path: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    description: str = ""
    revision: datetime | None = None


class SceneMediaRef(BaseModel):
    """环境媒体制作输入的冻结引用；沿用环境素材库身份，独立于伙伴动作。"""

    model_config = ConfigDict(extra="forbid")

    scene_id: ResourceId | None = None
    title: str = ""
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


class VisualCreationPlan(BaseModel):
    """具体活动计划：活动、起始画面、必要道具与运动要求。"""

    model_config = ConfigDict(extra="forbid")

    activity: str = Field(min_length=1, max_length=600)
    opening_description: str = Field(min_length=1, max_length=1800)
    required_props: list[str] = Field(default_factory=list, max_length=20)
    motion_requirements: str = Field(min_length=1, max_length=1800)


class ActorClipSource(BaseModel):
    """透明主体动画的冻结输入：身份、造型与动作；不固定背景。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["actor"] = "actor"
    identity: CharacterCardSnapshot
    identity_path: str = Field(min_length=1)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    portrait_path: str = Field(min_length=1)
    portrait_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_reference_role: CharacterReferenceRole = "portrait"
    outfit: ComboMaterialRef
    action: ActionMaterialRef
    plan: VisualCreationPlan | None = None
    requirements: str = ""
    feedback: str = ""
    video_feedback: str = ""
    persona: dict[str, str] = Field(default_factory=dict)

    @property
    def primary_reference_path(self) -> str:
        return self.identity_path if self.identity_reference_role == "fullbody" else self.portrait_path

    @property
    def primary_reference_hash(self) -> str:
        return self.identity_hash if self.identity_reference_role == "fullbody" else self.portrait_hash

    @property
    def identity_reference_mode(self) -> Literal["primary", "portrait_and_outfit"]:
        return "primary" if self.identity_reference_role == "fullbody" else "portrait_and_outfit"


class SceneMediaSource(BaseModel):
    """环境媒体的冻结输入：一张环境素材即可制作静态图或循环视频。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["scene"] = "scene"
    scene: SceneMediaRef
    requirements: str = ""
    feedback: str = ""
    video_feedback: str = ""
    persona: dict[str, str] = Field(default_factory=dict)


class CinematicSource(BaseModel):
    """完整场景影像的冻结输入：身份、造型、环境与动作的整体组合。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["cinematic"] = "cinematic"
    identity: CharacterCardSnapshot
    identity_path: str = Field(min_length=1)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    portrait_path: str = Field(min_length=1)
    portrait_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_reference_role: CharacterReferenceRole = "portrait"
    outfit: ComboMaterialRef
    scene: SceneMediaRef
    action: ActionMaterialRef
    plan: VisualCreationPlan | None = None
    requirements: str = ""
    feedback: str = ""
    video_feedback: str = ""
    persona: dict[str, str] = Field(default_factory=dict)

    @property
    def primary_reference_path(self) -> str:
        return self.identity_path if self.identity_reference_role == "fullbody" else self.portrait_path

    @property
    def primary_reference_hash(self) -> str:
        return self.identity_hash if self.identity_reference_role == "fullbody" else self.portrait_hash

    @property
    def identity_reference_mode(self) -> Literal["primary", "portrait_and_outfit"]:
        return "primary" if self.identity_reference_role == "fullbody" else "portrait_and_outfit"


VisualSource = Annotated[ActorClipSource | SceneMediaSource | CinematicSource, Field(discriminator="kind")]


class VisualSnapshot(BaseModel):
    """一次制作的完整冻结输入：判别 `source` 类型，另存目标画幅。

    旧组合结构已按目标架构直接替换；快照内容哈希按本结构计算。
    """

    model_config = ConfigDict(extra="forbid")

    source: VisualSource
    target_width: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)
    target_height: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)

    @property
    def kind(self) -> VisualAssetKind:
        return self.source.kind


class OpeningReview(BaseModel):
    """起始画面的检查结论，绑定图片、参考与要求摘要。"""

    model_config = ConfigDict(extra="forbid")

    image_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reference_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["complete", "unavailable", "invalid", "blocked"] = "complete"
    score: int | None = Field(default=None, ge=0, le=100, strict=True)
    verdict: Literal["pass", "review", "fail"] | None = None
    quality_verdict: Literal["pass", "review", "fail"] | None = None
    reason: str = ""


class ActorClipBody(BaseModel):
    """透明主体动画的主体元数据：遮罩、首末帧、锚点、命中区域与接续关系。

    坐标均以媒体原始像素为单位；mask 为逐帧或关键帧遮罩文件引用，不允许用
    单张静态遮罩冒充所有帧（计划 2 的制作链负责生成真实时变轮廓）。
    """

    model_config = ConfigDict(extra="forbid")

    mask_path: str | None = None
    first_frame_path: str | None = None
    last_frame_path: str | None = None
    foot_anchor: tuple[int, int] | None = None
    head_anchor: tuple[int, int] | None = None
    body_bounds: tuple[int, int, int, int] | None = None
    hit_bounds: tuple[int, int, int, int] | None = None
    continuity_key: str | None = None


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
    body: ActorClipBody | None = None
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


def visual_snapshot_hash(snapshot: VisualSnapshot) -> str:
    """冻结输入的内容哈希，用于校验快照未被改写。"""

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
    asset_kind: VisualAssetKind
    clip_kind: ActionClipKind | None = None
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
    asset_kind: VisualAssetKind
    # 冻结输入里的动作名称，供库内标识这一次制作。
    action_name: str = ""
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
    """成品详情：完整预览、冻结来源与实际媒体信息。"""

    snapshot: VisualSnapshot
    replaced_at_version: int | None = None
    jobs: list[VisualJobView] = Field(default_factory=list)


class ActorClipRef(BaseModel):
    """舞台组合引用的一份可播媒体（成品 ID + 版本号）；具体文件由播放指令携带。"""

    model_config = ConfigDict(extra="forbid")

    visual_id: ResourceId
    version: int = Field(ge=1)


class StageComposition(BaseModel):
    """当前有效的基础画面：分层组合或完整影像，二选一。

    分层模式引用环境层与主体层，两者可独立更换；完整影像模式直接引用成品，
    影像已包含伙伴本人时不另叠主体层。两者都是呈现配置，不是生活事实。
    """

    model_config = ConfigDict(extra="forbid")

    mode: StageMode
    scene_visual_id: ResourceId | None = None
    scene_version: int | None = Field(default=None, ge=1)
    actor: ActorClipRef | None = None
    cinematic: ActorClipRef | None = None

    @model_validator(mode="after")
    def valid_mode(self) -> "StageComposition":
        if self.mode == "layered":
            if self.cinematic is not None:
                raise ValueError("分层模式不能引用完整影像")
        elif self.cinematic is None:
            raise ValueError("完整影像模式必须引用具体成品")
        return self


class StageIntent(BaseModel):
    """生活情境希望呈现什么；只有生活发生有意义变化时才更新。"""

    model_config = ConfigDict(extra="forbid")

    activity: str = ""
    scene_title: str = ""
    mood: str = ""
    focus: str = ""
    activity_kind: str = ""
    visual_tags: list[str] = Field(default_factory=list, max_length=12)
    life_revision: int = 0


class StagePresentation(BaseModel):
    """静态呈现目标：分层降级时冻结已有造型与环境素材。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    presentation_epoch: int
    state_revision: int
    life_revision: int
    outfit: ComboMaterialRef | None = None
    scene: SceneMediaRef | None = None
    identity_path: str | None = None
    identity_hash: str | None = None
    receipt_kind: Literal["window_static", "desktop_static"] | None = None
    error: str | None = None
    retry_at: datetime | None = None


class StageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    presentation_epoch: int
    state_revision: int
    receipt_kind: Literal["window_static", "desktop_static"]
    status: Literal["started", "failed"]
    error: str | None = Field(default=None, max_length=2000)


class VisualPresentationView(BaseModel):
    """呈现状态与偏好：舞台意图、有效组合、待执行指令和自主开关。"""

    model_config = ConfigDict(extra="forbid")

    composition: StageComposition | None = None
    intent: StageIntent | None = None
    current_visual: VisualItemView | None = None
    loop_visual: VisualItemView | None = None
    autonomous_enabled: bool = True
    target_width: int | None = None
    target_height: int | None = None
    presentation_epoch: int = 0
    state_revision: int = 0
    pending_play: "VisualPlayCommand | None" = None
    static_presentation: StagePresentation | None = None


class VisualPlayCommand(BaseModel):
    """派发给客户端的演出指令；`queued` 只表示受理。

    分层模式携带组合引用与素材地址，客户端据此合成；完整影像模式只携带
    成品本身。短动作结束返回组合里的基础层。
    """

    model_config = ConfigDict(extra="forbid")

    play_id: str
    status: Literal["queued"] = "queued"
    asset_kind: VisualAssetKind
    visual_id: ResourceId
    visual_version: int
    media_kind: VisualMediaKind
    media_path: str
    poster_path: str | None = None
    input_image_hash: str | None = None
    clip_kind: ActionClipKind | None = None
    loop: bool
    transition: Literal["natural", "fade"] = "fade"
    presentation_epoch: int
    state_revision: int
    source: PlaybackSource
    expires_at: datetime
    composition: StageComposition | None = None
    outfit: ComboMaterialRef | None = None
    scene: SceneMediaRef | None = None


VisualPresentationView.model_rebuild()


class PresentedVisual(BaseModel):
    """播放时冻结的成品版本；重做采用不改变已显示画面的事实或文件引用。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: ResourceId
    user_id: ResourceId
    media_kind: VisualMediaKind
    asset_kind: VisualAssetKind = "cinematic"
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
    """重做所选影像：默认沿用冻结输入与已保存要求。"""

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
    asset_kind: VisualAssetKind | None = None
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
    asset_kind: VisualAssetKind
    clip_kind: ActionClipKind | None = None
    duration_seconds: int | None = None
    description: str
    outfit_description: str
    scene_description: str
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


class ActionMediaEntry(BaseModel):
    """动作详情里一条已制作的媒体实现投影。"""

    model_config = ConfigDict(extra="forbid")

    visual_id: ResourceId
    media_kind: VisualMediaKind
    asset_kind: VisualAssetKind
    clip_kind: ActionClipKind | None = None
    title: str = ""
    version: int
    outfit_id: ResourceId | None = None
    outfit_name: str = ""
    updated_at: datetime


class ActionMediaListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media: list[ActionMediaEntry]
    has_more: bool
    next_cursor: str | None = None
    limit: int
