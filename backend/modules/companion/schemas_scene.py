"""生活空间场景 REST 契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .scene import SceneOrigin, ScenePolicy, SceneSource, SceneStatus


class SceneImageDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)


class SceneImageSize(SceneImageDimensions):
    width: int = Field(gt=0, le=16384, strict=True)
    height: int = Field(gt=0, le=16384, strict=True)

    @model_validator(mode="after")
    def bounded_pixels(self) -> "SceneImageSize":
        if self.width * self.height > 64 * 1024 * 1024:
            raise ValueError("壁纸尺寸超过总像素上限")
        return self


class SceneRegenerationResponse(BaseModel):
    task_id: str
    status: Literal["pending", "ready", "failed", "cancelled"]
    stage: str
    error: str | None = None


class SceneResponse(BaseModel):
    id: int
    status: SceneStatus
    stage: str
    origin: SceneOrigin
    source: SceneSource
    target_size: SceneImageSize | None = None
    source_size: SceneImageDimensions | None = None
    image_size: SceneImageDimensions | None = None
    title: str = ""
    description: str = ""
    requirements: str = ""
    prompt: str = ""
    url: str = ""
    auto_activate: bool = False
    switch_version: int = 0
    error: str | None = None
    attempt_count: int = 0
    requested_at: datetime | None = None
    ready_at: datetime | None = None
    activated_at: datetime | None = None
    regeneration: SceneRegenerationResponse | None = None


class SceneListResponse(BaseModel):
    scenes: list[SceneResponse]
    total: int
    offset: int
    limit: int
    version: int


class SceneStateResponse(BaseModel):
    active: SceneResponse | None = None
    policy: ScenePolicy = ScenePolicy.LLM_MAY_REPLACE
    pending: SceneResponse | None = None
    regenerating: SceneResponse | None = None
    version: int = 0
    switch_version: int = 0


class SceneGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)
    image: str | None = Field(default=None, min_length=1, max_length=8 * 1024 * 1024)
    # 客户端声明的类型只做入口约束；场景服务按图片实际格式编码。
    content_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"] = "image/png"


class ScenePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)


class SceneActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: int = Field(gt=0)


class SceneDescriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=2000)

    @field_validator("title", "description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("场景标题和描述不能为空")
        return value.strip()


class ScenePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: ScenePolicy


class ScenePolicyResponse(BaseModel):
    policy: ScenePolicy
