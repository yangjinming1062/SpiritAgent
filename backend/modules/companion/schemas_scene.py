"""生活空间场景 REST 契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .scene import SceneOrigin, ScenePolicy, SceneSource, SceneStatus


class SceneResponse(BaseModel):
    id: int
    status: SceneStatus
    stage: str
    origin: SceneOrigin
    source: SceneSource
    title: str = ""
    description: str = ""
    requirements: str = ""
    outfit_description: str = ""
    prompt: str = ""
    url: str = ""
    seed_portrait_media_id: str = ""
    auto_activate: bool = False
    switch_version: int = 0
    error: str | None = None
    attempt_count: int = 0
    requested_at: datetime | None = None
    ready_at: datetime | None = None
    activated_at: datetime | None = None


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
    version: int = 0
    switch_version: int = 0


class SceneGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=2000)
    outfit_description: str | None = Field(default=None, max_length=2000)
    image: str | None = Field(default=None, min_length=1, max_length=8 * 1024 * 1024)
    content_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"] = "image/png"


class ScenePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=2000)
    outfit_description: str | None = Field(default=None, max_length=2000)


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
