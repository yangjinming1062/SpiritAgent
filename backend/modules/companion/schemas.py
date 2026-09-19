from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Persona blob 整体作为 JSON 字符串传输；32 KiB 在 HTTP 边界把 DoS 封顶，同时给最大 persona 字段（2000 字符）+ user_* 字段 + JSON 开销留余量。
_PERSONA_JSON_MAX_LEN: int = 32 * 1024


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_json: str = Field(min_length=1, max_length=_PERSONA_JSON_MAX_LEN)


class PersonaResponse(BaseModel):
    definition_json: str
    is_complete: bool
    personality_tags: list[str] = Field(default_factory=list)
    render_mode: str = "2d"
    current_mood: str | None = None


# 生成是同步的——所有持久化资产都是 succeeded；钉死字面量以便未来若改为异步时契约仍清楚。
SucceededStatus = Literal["succeeded"]

# 迭代修改意图：edit=微调（编辑上一版产物，未提及区域保留）；regenerate=重新生成（种子锚定全量重绘）。必传。
ImageReviseMode = Literal["edit", "regenerate"]


class AvatarAssetResponse(BaseModel):
    id: int
    asset_url: str
    seed_fullbody_url: str = ""
    seed_front_2d_url: str = ""
    seed_front_3d_url: str = ""
    seed_back_url: str = ""
    supports_multiview: bool = False
    prompt: str = ""
    status: SucceededStatus = "succeeded"


class FullbodyReferenceGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)
    mode: ImageReviseMode


class Fullbody2dFrontGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)
    mode: ImageReviseMode


# 3D 种子（A-pose 正面 / 背面）生成共用请求体；画风由服务端按物种路由并随行持久化，正背恒成对一致
class Fullbody3dSeedGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)
    mode: ImageReviseMode


class FullbodyConfirmFrontRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    front_url: str | None = Field(default=None, max_length=2048)


# 全身种子自备图点位；值与 REST 路径段一致
FullbodySeedKind = Literal["reference", "front-2d", "front-3d", "back"]


class FullbodyPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)


class FullbodyAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class ImagePromptResponse(BaseModel):
    prompt: str


class ImageAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class AvatarGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AvatarUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class AvatarFromImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 与上传相同的 8 MiB 上限：图同时是供应商 seed（经签名 URL）和供应商重新渲染的真相源。
    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    # 身份锚之外的呈现/风格参考图（可选）。
    presentation_image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    presentation_content_type: str | None = Field(default=None, max_length=64)


class AvatarHistoryResponse(BaseModel):
    history: list[AvatarAssetResponse]


class Companion3DModelResponse(BaseModel):
    id: int
    asset_url: str | None = None
    provider: str
    species: str = "人类"
    rig_type: str = "biped"
    rig_naming: str = "tripo"
    # 模型生成所用的 seed 风格，路由客户端渲染风格。
    style: str = "realistic"
    status: str = "succeeded"
    has_rig: bool
    content_hash: str | None = None
    # 语义键 → GLB 内 clip 名；客户端据此兑现动作，自身不持有任何供应商命名。
    clip_map: dict[str, str] = Field(default_factory=dict)


class ModelGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species_override: str | None = Field(default=None, max_length=64)
    provider: Literal["tripo", "hunyuan"] | None = None
    # False 幂等返回现有 active 模型；True 强制付费重新生成。
    force: bool = False


class Companion2DModelResponse(BaseModel):
    id: int
    status: str = "generating"
    manifest_url: str | None = None
    layer_urls: dict[str, str] = Field(default_factory=dict)
    content_hash: str | None = None
    error: str | None = None


class RenderModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_mode: Literal["2d", "3d"]


class OutfitCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=500)
    # 服装参考图（可选）：与身份锚点（正面种子）构成双参考，仅多参考图供应商消费
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class OutfitRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)
    mode: ImageReviseMode


class OutfitPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=500)
    # 服装参考图（可选）：整合为着装设计稿后进入提示词，不直传生图
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class OutfitAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=500)
    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class OutfitConfirmRequest(BaseModel):
    """确认入柜可选姿态图；字段语义见 PIPELINE §1.1.2。"""

    model_config = ConfigDict(extra="forbid")

    pose_left: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    pose_left_content_type: str | None = Field(default=None, max_length=64)
    pose_right: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    pose_right_content_type: str | None = Field(default=None, max_length=64)


class OutfitRegeneratePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)


class OutfitResponse(BaseModel):
    asset: Companion2DModelResponse | None = None
    id: int
    name: str
    description: str | None = None
    fullbody_url: str = ""
    # draft → splitting → ready | failed | expired
    status: str = "draft"
    active: bool = False
    # 确认即穿着：切分完成后自动换上；期间手动穿着其他装会清掉该标记
    pending_wear: bool = False


class OutfitListResponse(BaseModel):
    outfits: list[OutfitResponse]
    policy: Literal["locked", "llm_may_replace"] = "llm_may_replace"


class OutfitPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["locked", "llm_may_replace"]


class OutfitPolicyResponse(BaseModel):
    policy: Literal["locked", "llm_may_replace"]


class VoiceEntry(BaseModel):
    id: str
    provider: str
    label: str
    gender: str
    language: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""


class VoicesListResponse(BaseModel):
    providers: list[str]
    voices: list[VoiceEntry]
    supports_voice_design: bool
    voice_design_guide: str


class VoiceMatchResponse(BaseModel):
    voice: VoiceEntry | None
    alternatives: list[VoiceEntry]


class OnboardingStateResponse(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    next_field: str | None = None
    complete: bool = False


class CompanionOperationResponse(BaseModel):
    ok: bool = True
