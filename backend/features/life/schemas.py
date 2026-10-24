"""生活状态的公开 DTO 与请求。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LifeStateView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    current_activity: str
    current_scene: dict
    mood: str | None
    active_pursuit_id: int | None
    next_review_at: datetime | None
    revision: int
    scene_revision: int
    updated_at: datetime


class LifePursuitView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    reason: str
    desired_result: str
    progress: str
    next_step: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    result_ref: dict
    updated_at: datetime


class LifeStepView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pursuit_id: int | None
    idempotency_key: str
    kind: str
    description: str
    state: str
    business_ref: dict
    result_ref: dict
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class LifeEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_key: str
    kind: str
    source: str
    subject: str
    fact: dict
    occurred_at: datetime
    pursuit_id: int | None
    step_id: int | None
    result_ref: dict
    is_meaningful: bool


class LifeContext(BaseModel):
    """所有伙伴推理共同读取的生活快照。"""

    model_config = ConfigDict(extra="forbid")

    state: LifeStateView
    active_pursuits: list[LifePursuitView] = Field(default_factory=list)
    recent_events: list[LifeEventView] = Field(default_factory=list)

    def prompt_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class LifePursuitListResponse(BaseModel):
    items: list[LifePursuitView] = Field(default_factory=list)
    total: int


class LifeEventListResponse(BaseModel):
    items: list[LifeEventView] = Field(default_factory=list)
    total: int


class LifeDecisionRequest(BaseModel):
    """内部/后续 workflow 使用的乐观提交 DTO；HTTP 仅提供只读入口。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    current_activity: str | None = Field(default=None, max_length=2000)
    current_scene: dict | None = None
    mood: str | None = Field(default=None, max_length=200)
    next_review_at: datetime | None = None
    scene_changed: bool = False


class LifeEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1, max_length=180)
    kind: str = Field(min_length=1, max_length=48)
    source: str = Field(min_length=1, max_length=48)
    subject: Literal["companion", "user", "system"] = "companion"
    fact: dict = Field(default_factory=dict)
    occurred_at: datetime | None = None
    pursuit_id: int | None = Field(default=None, gt=0)
    step_id: int | None = Field(default=None, gt=0)
    result_ref: dict = Field(default_factory=dict)
    is_meaningful: bool = True
