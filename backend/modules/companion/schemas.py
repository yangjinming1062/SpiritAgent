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
    current_mood: str | None = None


# 生成是同步的——所有持久化资产都是 succeeded；钉死字面量以便未来若改为异步时契约仍清楚。
SucceededStatus = Literal["succeeded"]

# 迭代修改意图：edit=微调（编辑上一版产物，未提及区域保留）；regenerate=重新生成（种子锚定全量重绘）。必传。
ImageReviseMode = Literal["edit", "regenerate"]


class AvatarAssetResponse(BaseModel):
    id: int
    asset_url: str
    seed_fullbody_url: str = ""
    is_fullbody_confirmed: bool = False
    prompt: str = ""
    status: SucceededStatus = "succeeded"


class FullbodyCandidateResponse(BaseModel):
    id: int
    avatar_id: int
    image_url: str
    status: Literal["pending", "ready", "failed", "accepted", "rejected"]
    error: str | None = None


class MediaReviewResponse(BaseModel):
    id: int
    status: Literal["pending", "accepted", "rejected"]
    reason: str = ""
    media_type: Literal["image", "video"]
    media_url: str
    title: str = ""


class FullbodyReferenceGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)
    mode: ImageReviseMode
    candidate_id: int | None = Field(default=None, gt=0)


class FullbodyConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_url: str = Field(min_length=1, max_length=2048)


class PortraitConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_avatar_id: int = Field(gt=0)


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

    feedback: str | None = Field(default=None, max_length=500)


class AvatarPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)
    has_reference: bool = False


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


class OutfitCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=500)
    # 服装参考图（可选）：先与文字要求整合为着装描述，不直传生图
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
    """确认不接受图片或其他字段。"""

    model_config = ConfigDict(extra="forbid")


class OutfitRegeneratePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)


class OutfitResponse(BaseModel):
    initial_video_error: str | None = None
    id: int
    name: str
    description: str | None = None
    fullbody_url: str = ""
    # draft → ready | failed | expired；ready 表示参考图就绪
    status: str = "draft"
    active: bool = False


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
