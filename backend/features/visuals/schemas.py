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

from features.action_library.schemas import ActionAssetDesign
from features.life.schemas import FactType
from features.media.identity_contracts import CharacterCardSnapshot, CharacterReferenceRole
from features.media.video_queue.contracts import VideoQueueInfo
from features.media.visual_semantics import (
    ActionMotionIntensity,
    ActionPerformanceRole,
    ActivityType,
    DayPhase,
    SceneTags,
    SemanticTags,
)

VISUAL_MAX_EDGE = 65536

VisualMediaKind = Literal["image", "video"]
VisualAssetKind = Literal["actor", "scene", "cinematic"]
VisualCreationSource = Literal["user_requested", "autonomous", "base_preparation"]
ActionClipKind = Literal["once", "loop"]
VisualJobState = Literal["queued", "running", "paused", "succeeded", "failed", "cancelled", "unknown"]
PlaybackStatus = Literal["queued", "claimed", "started", "finished", "failed", "expired"]
PlaybackReceiptKind = Literal["image", "video", "window_static"]
# manual=用户点播、autonomous=伙伴自主选择、life=生活导演按情境选择、restore=恢复既有呈现。
PlaybackSource = Literal["manual", "autonomous", "life", "restore"]
StageMode = Literal["layered", "cinematic"]
# 持续画面的选择来源；决定生活意图变化时是否重新核对（见 StageComposition.selection_source）。
SelectionSource = Literal["base", "life", "user"]


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
    semantic_tags: SemanticTags
    performance_role: ActionPerformanceRole
    motion_intensity: ActionMotionIntensity
    interruptible: bool
    use_when: list[str] = Field(max_length=8)
    avoid_when: list[str] = Field(max_length=8)
    allowed_triggers: list[str] = Field(max_length=8)
    revision: datetime | None = None

    def media_basis(self) -> dict:
        """影响媒体内容的动作版本；名称、数据库身份与描述分析时间不重置制作。"""

        return self.model_dump(mode="json", exclude={"name", "revision", "action_id"})


class VisualCreationPlan(BaseModel):
    """具体活动计划：活动、起始画面、必要道具与运动要求。"""

    model_config = ConfigDict(extra="forbid")

    activity: str = Field(min_length=1, max_length=600)
    opening_description: str = Field(min_length=1, max_length=1800)
    required_props: list[str] = Field(default_factory=list, max_length=20)
    motion_requirements: str = Field(min_length=1, max_length=1800)


class MediaSemantics(BaseModel):
    """从视频采样确认的实际表现与适用条件，不继承动作的全部通用用途。"""

    model_config = ConfigDict(extra="forbid")

    activity_type: ActivityType
    semantic_tags: SemanticTags = Field(default_factory=list)
    motion_intensity: ActionMotionIntensity
    auto_selectable: bool = Field(
        description="实际表现与适用条件是否足以支持按当前活动和环境自动选择；未表达的必要条件仍待确认时为 false"
    )
    scene_tags: SceneTags = Field(
        default_factory=list,
        description="播放该片段需要符合的环境和物品条件；自带的随身物品不要求外部提供，无法确认时不补写",
    )
    avoid_activity_types: list[ActivityType] = Field(default_factory=list, max_length=8)
    avoid_scene_tags: SceneTags = Field(default_factory=list)


class VisualSourceMetadata(BaseModel):
    """可选的原始文件与内容理解记录；不代表影像的业务类型。"""

    model_config = ConfigDict(extra="forbid")

    original_filename: str | None = Field(default=None, min_length=1, max_length=255)
    original_path: str | None = Field(default=None, min_length=1)
    original_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    original_width: int | None = Field(default=None, gt=0)
    original_height: int | None = Field(default=None, gt=0)
    original_duration_ms: int | None = Field(default=None, gt=0)
    analysis_job_id: ResourceId | None = None
    analysis_revision: int | None = None
    analysis_pending: bool = False
    presentation_approved: bool = True
    identity_reason: str = ""


class ActorClipSource(VisualSourceMetadata):
    """透明主体动画的冻结输入：身份、造型与动作；不固定背景。"""

    kind: Literal["actor"] = "actor"
    identity: CharacterCardSnapshot
    identity_path: str = Field(min_length=1)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    portrait_path: str = Field(min_length=1)
    portrait_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_reference_role: CharacterReferenceRole = "portrait"
    outfit: ComboMaterialRef
    action: ActionMaterialRef
    media_semantics: MediaSemantics | None = None
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


class SceneMediaSource(VisualSourceMetadata):
    """环境媒体的冻结输入：一张环境素材即可制作静态图或循环视频。"""

    kind: Literal["scene"] = "scene"
    scene: SceneMediaRef
    # 冻结的适用时段；随版本保存，重做或定义编辑不追改历史版本。
    day_phase: DayPhase | None = None
    requirements: str = ""
    feedback: str = ""
    video_feedback: str = ""
    persona: dict[str, str] = Field(default_factory=dict)


class CinematicSource(VisualSourceMetadata):
    """完整场景影像的冻结输入：身份、造型、环境与动作的整体组合。"""

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
    media_semantics: MediaSemantics | None = None
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


class ImportObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    kind: Literal["outfit", "scene", "action"]
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1500)
    features: list[str] = Field(default_factory=list, max_length=12)
    frame_index: int = Field(ge=0, le=23)
    crop: tuple[float, float, float, float] | None = None
    extractable: bool
    extraction_reason: str = Field(min_length=1, max_length=600)
    action_design: ActionAssetDesign | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_region(self) -> "ImportObservation":
        if self.crop is not None:
            left, top, right, bottom = self.crop
            if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError("提取区域必须是有效的归一化矩形")
        if self.end_seconds is not None and (self.start_seconds is None or self.end_seconds <= self.start_seconds):
            raise ValueError("动作片段时间无效")
        if self.kind != "action" and self.action_design is not None:
            raise ValueError("只有动作识别可以提供动作定义")
        return self


class ImportUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1500)
    asset_kind: VisualAssetKind
    identity_match: Literal["same", "different", "uncertain", "absent"]
    identity_reason: str = Field(min_length=1, max_length=600)
    presentation_verdict: Literal["pass", "review", "fail"]
    presentation_reason: str = Field(min_length=1, max_length=600)
    clip_kind: ActionClipKind | None = None
    media_semantics: MediaSemantics | None = None
    observations: list[ImportObservation] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_keys(self) -> "ImportUnderstanding":
        if len({entry.key for entry in self.observations}) != len(self.observations):
            raise ValueError("识别条目标识不能重复")
        return self


class ImportMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: ResourceId
    name: str
    decision: Literal["same", "different", "uncertain"]
    confidence: Literal["high", "medium", "low"]
    evidence: str = Field(min_length=1, max_length=600)


class ImportComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: ImportObservation
    matches: list[ImportMatch] = Field(default_factory=list)
    status: Literal["recognized", "ambiguous", "reused", "created", "reviewing", "failed", "skipped", "undone"] = (
        "recognized"
    )
    material_id: ResourceId | None = None
    created: bool = False
    extraction_key: str = ""
    reason: str = ""
    preview_path: str | None = None


class ImportAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    understanding: ImportUnderstanding
    components: list[ImportComponent] = Field(default_factory=list)


class ImportAnalysisView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ResourceId
    state: VisualJobState
    stage: str
    revision: int
    error: str | None = None
    result: ImportAnalysisResult | None = None
    updated_at: datetime


class ImportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=32)
    command: Literal["existing", "extract", "skip", "undo"]
    material_id: ResourceId | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_existing(self) -> "ImportSelection":
        if (self.command == "existing") != (self.material_id is not None):
            raise ValueError("只有选择已有素材时提供素材 ID")
        return self


class ImportAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=0)
    command: Literal["retry", "cancel", "apply", "reanalyze"] = "retry"
    selections: list[ImportSelection] = Field(default_factory=list, max_length=12)
    notes: str | None = Field(default=None, max_length=2000)


class AnalyzingVisualSource(VisualSourceMetadata):
    """影像尚在内容理解阶段的暂存来源；完成后恢复为普通影像来源。"""

    kind: Literal["analysis"] = "analysis"
    asset_kind: VisualAssetKind = "cinematic"
    identity: CharacterCardSnapshot | None = None
    identity_path: str | None = None
    identity_hash: str | None = None
    portrait_path: str | None = None
    portrait_hash: str | None = None
    identity_reference_role: CharacterReferenceRole = "fullbody"
    outfit: ComboMaterialRef | None = None
    scene: SceneMediaRef | None = None
    action: ActionMaterialRef | None = None
    media_semantics: MediaSemantics | None = None
    clip_kind: ActionClipKind | None = None
    original_filename: str = Field(min_length=1, max_length=255)
    original_path: str = Field(min_length=1)
    original_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    presentation_approved: bool = False
    analysis_pending: bool = True


VisualSource = Annotated[
    ActorClipSource | SceneMediaSource | CinematicSource | AnalyzingVisualSource, Field(discriminator="kind")
]


class VisualSnapshot(BaseModel):
    """一次制作的完整冻结输入：判别 `source` 类型，另存目标画幅。

    旧组合结构已按目标架构直接替换；快照内容哈希按本结构计算。
    """

    model_config = ConfigDict(extra="forbid")

    source: VisualSource
    target_width: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)
    target_height: int = Field(gt=0, le=VISUAL_MAX_EDGE, strict=True)
    review_reason: str = Field(default="", description="已发布成品的实际检查依据；制作输入为空")

    @property
    def kind(self) -> VisualAssetKind:
        return self.source.asset_kind if self.source.kind == "analysis" else self.source.kind


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
    单张静态遮罩代替整段轮廓。
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
    media_semantics: MediaSemantics | None = None
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
    source: VisualCreationSource
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
    # actor 主体元数据投影；路径已在响应出口转为客户端地址。
    body: ActorClipBody | None = None
    auto_playable: bool = Field(default=False, description="成品能否参与自动演出；不判断当前舞台是否适用")
    analysis_state: VisualJobState | None = None
    original_url: str | None = None


class StageLayerMedia(BaseModel):
    """舞台某一层的可播媒体投影；客户端据此合成分层画面。"""

    model_config = ConfigDict(extra="forbid")

    visual_id: ResourceId
    version: int
    media_kind: VisualMediaKind
    asset_kind: VisualAssetKind
    clip_kind: ActionClipKind | None = None
    media_url: str
    poster_url: str | None = None
    width: int
    height: int
    duration_ms: int | None = None
    description: str = ""
    body: ActorClipBody | None = None
    # 环境层适用时段；空表示不限时段。时间版本是视觉表现，不是生活事实。
    day_phase: DayPhase | None = None
    scene_content_hash: str | None = None


class StageCompositionMedia(BaseModel):
    """有效舞台组合的媒体投影：分层引用环境层与主体层，完整影像只引用整片。"""

    model_config = ConfigDict(extra="forbid")

    mode: StageMode
    scene: StageLayerMedia | None = None
    actor: StageLayerMedia | None = None
    cinematic: StageLayerMedia | None = None


class VisualJobView(BaseModel):
    """制作工作项对外视图。"""

    model_config = ConfigDict(extra="forbid")

    id: ResourceId
    purpose: str
    target_kind: VisualMediaKind
    asset_kind: VisualAssetKind
    # 冻结输入里的动作名称，供库内标识这一次制作。
    action_name: str = ""
    # 基础动态准备的工作项；用于区分自动准备与用户/自主制作。
    preparation: bool = False
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
    analysis: ImportAnalysisView | None = None


class ActorClipRef(BaseModel):
    """舞台组合引用的一份可播媒体（成品 ID + 版本号）；具体文件由播放指令携带。"""

    model_config = ConfigDict(extra="forbid")

    visual_id: ResourceId
    version: int = Field(ge=1)


StageLayerName = Literal["scene", "actor", "cinematic", "effects"]
StageFit = Literal["contain", "cover"]


class StageCompositionLayout(BaseModel):
    """舞台组合的呈现几何；不包含生活事实。"""

    model_config = ConfigDict(extra="forbid")

    layer_order: list[StageLayerName] = Field(
        default_factory=lambda: ["scene", "actor", "effects"], min_length=1, max_length=4
    )
    fit: StageFit = "contain"
    actor_position: tuple[float, float] = (0.5, 1.0)
    safe_area: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

    @model_validator(mode="after")
    def valid_geometry(self) -> "StageCompositionLayout":
        if len(set(self.layer_order)) != len(self.layer_order):
            raise ValueError("舞台图层顺序不能重复")
        if not all(0 <= value <= 1 for value in self.actor_position):
            raise ValueError("主体位置必须在安全区域比例范围内")
        left, top, right, bottom = self.safe_area
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("安全区域必须是有效的归一化矩形")
        return self


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
    outfit_material: ComboMaterialRef | None = None
    scene_material: SceneMediaRef | None = None
    identity_path: str | None = None
    identity_hash: str | None = None
    layout: StageCompositionLayout
    # 持续画面为什么被选择：base=普通基础舞台、life=生活导演、user=用户/伙伴点播。
    # life 来源记录选择时的生活修订与活动类别；生活变化后据此重新核对该画面是否仍然适用，
    # 不匹配时退出回基础舞台。user 来源不受普通生活更新影响，直到用户换选或素材失效。
    selection_source: SelectionSource
    life_revision: int | None = Field(default=None, ge=0)
    life_activity_type: ActivityType | None = None
    # 各层媒体投影；由查询按引用版本填充，桌面与窗口共用同一份数据。
    media: StageCompositionMedia | None = None

    @model_validator(mode="after")
    def valid_mode(self) -> "StageComposition":
        if self.mode == "layered":
            if self.cinematic is not None:
                raise ValueError("分层模式不能引用完整影像")
        elif self.cinematic is None:
            raise ValueError("完整影像模式必须引用具体成品")
        elif self.actor is not None or self.scene_visual_id is not None or self.scene_version is not None:
            raise ValueError("完整影像模式不能叠加主体或环境引用")
        if (self.scene_visual_id is None) != (self.scene_version is None):
            raise ValueError("环境引用必须同时指定身份与版本")
        # 生活上下文只对生活驱动的完整影像持续画面有意义；其余来源不携带。
        if self.selection_source != "life" and (self.life_revision is not None or self.life_activity_type is not None):
            raise ValueError("生活上下文只在生活驱动的组合上记录")
        if self.selection_source == "life" and (self.life_revision is None or self.life_activity_type is None):
            raise ValueError("生活驱动的组合必须记录选择时的生活上下文")
        return self


class StageIntent(BaseModel):
    """生活情境希望呈现什么；只有生活发生有意义变化时才更新。"""

    model_config = ConfigDict(extra="forbid")

    activity: str = ""
    scene_title: str = ""
    mood: str = ""
    focus: str = ""
    activity_kind: FactType = "virtual_activity"
    activity_type: ActivityType = "idle"
    scene_tags: SceneTags = Field(default_factory=list)
    visual_tags: SemanticTags = Field(default_factory=list)
    life_revision: int = Field(default=0, ge=0)


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


class AmbientCatalogEntry(StageLayerMedia):
    """导演可选的常驻/反应媒体目录项；动作语义来自冻结输入，供本地选择。"""

    model_config = ConfigDict(extra="forbid")

    action_id: ResourceId | None = None
    action_name: str = ""
    clip_kind: ActionClipKind | None = None
    preset_key: str | None = None
    semantic_tags: SemanticTags = Field(default_factory=list)
    performance_role: ActionPerformanceRole
    motion_intensity: ActionMotionIntensity = "normal"
    interruptible: bool = True
    media_semantics: MediaSemantics | None = None
    allowed_triggers: list[str] = Field(default_factory=list, max_length=8)
    use_when: list[str] = Field(default_factory=list, max_length=8)
    avoid_when: list[str] = Field(default_factory=list, max_length=8)


class VisualPresentationView(BaseModel):
    """呈现状态与偏好：舞台意图、有效组合、待执行指令和自主开关。"""

    model_config = ConfigDict(extra="forbid")

    composition: StageComposition | None = None
    ambient_actor: StageLayerMedia | None = None
    ambient_catalog: list[AmbientCatalogEntry] = Field(default_factory=list)
    # 环境层目录与主体目录分开，避免完整场景成品挤占基础动作的可用数量。
    ambient_scene: list[StageLayerMedia] = Field(default_factory=list)
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
    成品本身。短动作结束返回组合里的基础层。`source` 是这次播放的选择来源，
    播放账本与持续画面来源都以它为准；`origin` 是网关入口要求的语义细分，
    与 `source` 一一对应（manual/user、life/life、restore/restore）。
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
    origin: Literal["user", "life", "restore"] = "user"
    expires_at: datetime
    composition: StageComposition | None = None
    outfit: ComboMaterialRef | None = None
    scene: SceneMediaRef | None = None
    # actor 主体元数据（锚点/命中区域）；路径已在响应出口转换。
    body: ActorClipBody | None = None
    day_phase: DayPhase | None = None


VisualPresentationView.model_rebuild()


class PresentedVisual(BaseModel):
    """播放时冻结的成品版本；重做采用不改变已显示画面的事实或文件引用。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: ResourceId
    user_id: ResourceId
    media_kind: VisualMediaKind
    asset_kind: VisualAssetKind
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
    # actor 版本冻结的主体元数据；随版本保存，播放指令据此携带锚点。
    body_json: dict | None = None
    play_id: str | None = None
    receipt_kind: PlaybackReceiptKind | None = None
    presentation_epoch: int | None = None


class VisualRegenerateRequest(BaseModel):
    """重做所选影像：默认沿用冻结输入与已保存要求。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    feedback: str | None = Field(default=None, max_length=2000)
    video_feedback: str | None = Field(default=None, max_length=2000)


class CinematicRequest(BaseModel):
    """用户选择现有素材制作完整图片或视频。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    outfit_id: ResourceId = Field(gt=0)
    scene_id: ResourceId = Field(gt=0)
    action_id: ResourceId = Field(gt=0)
    media_kind: VisualMediaKind = "image"
    requirements: str = Field(default="", max_length=2000)


class ActorClipRequest(BaseModel):
    """制作透明主体动画：造型与动作即可，不绑定环境。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    outfit_id: ResourceId = Field(gt=0)
    action_id: ResourceId = Field(gt=0)
    requirements: str = Field(default="", max_length=2000)
    feedback: str = Field(default="", max_length=2000)
    video_feedback: str = Field(default="", max_length=2000)


class SceneMediaRequest(BaseModel):
    """制作环境循环视频：单张已就绪的环境素材。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    scene_id: ResourceId = Field(gt=0)
    # 可选适用时段：为同一环境制作特定光线版本；空表示不限时段的普通版本。
    day_phase: DayPhase | None = None
    requirements: str = Field(default="", max_length=2000)
    feedback: str = Field(default="", max_length=2000)
    video_feedback: str = Field(default="", max_length=2000)


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
    semantic_tags: SemanticTags = Field(default_factory=list)
    performance_role: ActionPerformanceRole
    motion_intensity: ActionMotionIntensity = "normal"
    media_semantics: MediaSemantics | None = None
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
    # 与当前舞台冻结身份、造型一致且文件完整。
    stage_ready: bool = False
    updated_at: datetime


class ActionMediaListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media: list[ActionMediaEntry]
    has_more: bool
    next_cursor: str | None = None
    limit: int
