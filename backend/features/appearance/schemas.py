from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SucceededStatus = Literal["succeeded"]


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

    # 与上传同为 8 MiB：图同时是供应商 seed（经签名 URL）和重新渲染的真相源。
    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    # 身份锚之外的呈现参考图（光线、色调与构图，可选）。
    presentation_image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    presentation_content_type: str | None = Field(default=None, max_length=64)


class AvatarHistoryResponse(BaseModel):
    history: list[AvatarAssetResponse]


class OutfitCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=500)
    # 服装参考图（可选）：整合为着装描述后进入提示词，不直传生图
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
    model_config = ConfigDict(extra="forbid")


class OutfitRegeneratePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(default=None, max_length=500)


class OutfitResponse(BaseModel):
    initial_video_error: str | None = None
    description_status: Literal["pending", "processing", "ready", "failed"] = "pending"
    description_error: str | None = None
    id: int
    name: str
    description: str | None = None
    fullbody_url: str = ""
    # draft → ready | expired
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
