from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Persona blob 整体作为 JSON 字符串传输；32 KiB 在 HTTP 边界把 DoS 封顶，同时给最大 persona 字段（2000 字符）+ user_* 字段 + JSON 开销留余量。
_PERSONA_JSON_MAX_LEN: int = 32 * 1024


def normalize_persona_aliases(d: dict[str, Any]) -> dict[str, Any]:
    """将 species 归一化为 biological_type，character_gender 归一化为 gender。

    若别名与规范名共存，弹出并丢弃别名，保留规范名。
    """
    res = dict(d)
    if "species" in res:
        species_val = res.pop("species")
        if "biological_type" not in res:
            res["biological_type"] = species_val
    if "character_gender" in res:
        gender_val = res.pop("character_gender")
        if "gender" not in res:
            res["gender"] = gender_val
    return res


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


class AvatarAssetResponse(BaseModel):
    id: int
    asset_url: str
    seed_front_2d_url: str = ""
    seed_front_3d_url: str = ""
    seed_back_url: str = ""
    supports_multiview: bool = False
    # 已选 fullbody 风格；与 AvatarAsset.seed_front_2d_url 组合作为全身确认阶段的 resume 入口。
    fullbody_style: str = ""
    prompt: str = ""
    status: SucceededStatus = "succeeded"


class Fullbody2dFrontGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = Field(default="cel_shading", max_length=64)
    feedback: str | None = Field(default=None, max_length=500)
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


# 3D 种子（A-pose 正面 / 背面）生成共用请求体；画风由服务端按物种路由并随行持久化，正背恒成对一致
class Fullbody3dSeedGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)


class FullbodyConfirmFrontRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str | None = Field(default=None, max_length=64)
    front_url: str | None = Field(default=None, max_length=2048)


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
    style: str = "cel_shading"
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


class OutfitResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    fullbody_url: str = ""
    style: str = "cel_shading"
    # draft → splitting → ready | failed | expired
    status: str = "draft"
    active: bool = False
    # 确认即穿着：切分完成后自动换上；期间手动穿着其他装会清掉该标记
    pending_wear: bool = False


class OutfitListResponse(BaseModel):
    outfits: list[OutfitResponse]


class VoiceEntry(BaseModel):
    id: str
    label: str
    gender: str
    language: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""


class VoicesListResponse(BaseModel):
    provider: str = ""
    voices: list[VoiceEntry] = Field(default_factory=list)
    default_voice: VoiceEntry
    supports_voice_design: bool = False
    voice_design_guide: str = ""


class OnboardingStateResponse(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    next_field: str | None = None
    complete: bool = False


class ExpressionsListResponse(BaseModel):
    expressions: list[dict] = Field(default_factory=list)


class CompanionOperationResponse(BaseModel):
    ok: bool = True
