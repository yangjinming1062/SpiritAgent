"""生活空间场景 REST 契约。"""

from datetime import datetime
from typing import Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from features.appearance.schemas import ImageAdoptRequest
from features.scenes.models import SceneOrigin, ScenePolicy, SceneSource, SceneStatus


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
    id: ResourceId
    status: SceneStatus
    stage: str
    can_retry_generation: bool = False
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
    error: str | None = None
    attempt_count: int = 0
    requested_at: datetime | None = None
    ready_at: datetime | None = None
    regeneration: SceneRegenerationResponse | None = None


class SceneListResponse(BaseModel):
    scenes: list[SceneResponse]
    total: int | None = None
    has_more: bool
    next_cursor: str | None = None
    limit: int


class SceneStateResponse(BaseModel):
    """环境素材清单与政策；素材状态含准备中的任务，不表示当前所在环境。"""

    policy: ScenePolicy = ScenePolicy.LLM_MAY_REPLACE
    materials: list[SceneResponse] = Field(default_factory=list)
    generation_pending: bool = False


class SceneGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["generate"]

    notes: str | None = Field(default=None, max_length=500, pattern=r"^[^\x00]*$")
    image: str | None = Field(default=None, min_length=1, max_length=8 * 1024 * 1024)
    # 客户端声明的类型只做入口约束；场景服务按图片实际格式编码。
    content_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"] = "image/png"


class ScenePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500, pattern=r"^[^\x00]*$")


class SceneDescriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00]*$")
    description: str = Field(min_length=1, max_length=2000, pattern=r"^[^\x00]*$")

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


class SceneImportRequest(ImageAdoptRequest):
    source: Literal["analysis"]
    scene_id: ResourceId | None = Field(default=None, gt=0)
