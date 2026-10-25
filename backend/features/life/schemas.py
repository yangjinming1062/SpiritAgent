"""生活状态的公开 DTO 与请求。"""

from datetime import datetime
from typing import Any, Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field, field_serializer

UNRESOLVED_STEP_STATES = frozenset({"planned", "running", "unknown"})
TERMINAL_STEP_STATES = frozenset({"completed", "failed", "cancelled", "unknown"})
EVIDENCE_EVENT_KINDS = frozenset({"life.step.completed", "visual.ready", "post.published", "diary.published"})
EVIDENCE_EVENTS_PER_PURSUIT = 24


_REFERENCE_KEYS = frozenset(
    {
        "job_id",
        "entry_id",
        "visual_id",
        "visual_version_id",
        "pursuit_id",
        "step_id",
        "event_id",
        "post_id",
        "diary_id",
        "avatar_id",
        "outfit_id",
        "scene_id",
        "action_id",
    }
)
_REFERENCE_LIST_KEYS = frozenset({"event_ids", "evidence_event_ids", "step_ids"})


def _wire_result(value: Any) -> Any:
    if isinstance(value, list):
        return [_wire_result(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in _REFERENCE_KEYS and isinstance(item, int) and not isinstance(item, bool):
            result[key] = str(item)
        elif key in _REFERENCE_LIST_KEYS and isinstance(item, list):
            result[key] = [
                str(identifier) if isinstance(identifier, int) and not isinstance(identifier, bool) else identifier
                for identifier in item
            ]
        else:
            result[key] = _wire_result(item)
    return result


class LifeResultView(BaseModel):
    @field_serializer("result_ref", "business_ref", "fact", check_fields=False, when_used="json")
    def serialize_references(self, value: dict) -> dict:
        return _wire_result(value)


class LifeStateView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: ResourceId
    current_activity: str
    current_scene: dict
    mood: str | None
    active_pursuit_id: ResourceId | None
    next_review_at: datetime | None
    revision: int
    scene_revision: int
    updated_at: datetime


class LifePursuitView(LifeResultView):
    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
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


class LifeStepView(LifeResultView):
    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    pursuit_id: ResourceId | None
    idempotency_key: str
    kind: str
    description: str
    state: str
    business_ref: dict
    result_ref: dict
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class LifeEventView(LifeResultView):
    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    event_key: str
    kind: str
    source: str
    subject: str
    fact: dict
    occurred_at: datetime
    pursuit_id: ResourceId | None
    step_id: ResourceId | None
    result_ref: dict
    is_meaningful: bool


class LifeContext(BaseModel):
    """所有伙伴推理共同读取的生活快照。"""

    model_config = ConfigDict(extra="forbid")

    state: LifeStateView
    active_pursuits: list[LifePursuitView] = Field(default_factory=list)
    recent_events: list[LifeEventView] = Field(default_factory=list)

    def prompt_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "state": {"user_id", "next_review_at", "scene_revision", "updated_at"},
                "recent_events": {"__all__": {"event_key"}},
            },
        )


class LifePursuitListResponse(BaseModel):
    items: list[LifePursuitView] = Field(default_factory=list)
    total: int | None = None
    has_more: bool
    next_cursor: str | None


class LifeEventListResponse(BaseModel):
    items: list[LifeEventView] = Field(default_factory=list)
    total: int | None = None
    has_more: bool
    next_cursor: str | None


class LifeDecisionFields(BaseModel):
    """模型判断与领域提交共用的生活操作。"""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["keep", "update", "start_pursuit", "advance_pursuit", "complete_pursuit"] = "keep"
    current_activity: str | None = Field(
        default=None, max_length=2000, description="伙伴当前的具体活动；不变时省略或为 null"
    )
    current_scene: dict | None = Field(
        default=None, description="更新后的完整情境信息，保留未改变的内容；不变时省略或为 null"
    )
    mood: str | None = Field(default=None, max_length=200, description="伙伴当前心情；不变时省略或为 null")
    pursuit_id: ResourceId | None = Field(default=None, gt=0, description="要推进或完成的已有事情的 id")
    pursuit_title: str = Field(default="", max_length=160, description="新事情的简短标题")
    pursuit_reason: str = Field(default="", max_length=1000, description="现在开始这件事的具体缘由")
    pursuit_result: str = Field(default="", max_length=1000, description="怎样才算完成这件事，须能根据结果判断")
    progress: str | None = Field(
        default=None, max_length=2000, description="已有结果的总结；不是下一步打算，不变时省略或为 null"
    )
    next_step: str | None = Field(default=None, max_length=1000, description="接下来打算做什么；不代表已经执行")
    evidence_event_ids: list[ResourceId] = Field(
        default_factory=list,
        max_length=EVIDENCE_EVENTS_PER_PURSUIT,
        description="支持进展或完成总结的已提供事件 id",
    )


class LifeDecisionRequest(LifeDecisionFields):
    """内部乐观提交 DTO；HTTP 仅提供只读入口。"""

    expected_revision: int = Field(ge=0)
    next_review_at: datetime | None = None


class LifeStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=32)
    description: str
    pursuit_id: ResourceId | None = Field(default=None, gt=0)


class LifePursuitEvidence(BaseModel):
    steps: list[LifeStepView] = Field(default_factory=list)
    events: list[LifeEventView] = Field(default_factory=list)

    def prompt_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "steps": {"__all__": {"idempotency_key", "business_ref"}},
                "events": {"__all__": {"event_key"}},
            },
        )


class LifeEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1, max_length=180)
    kind: str = Field(min_length=1, max_length=48)
    source: str = Field(min_length=1, max_length=48)
    subject: Literal["companion", "user", "system"] = "companion"
    fact: dict = Field(default_factory=dict)
    occurred_at: datetime | None = None
    pursuit_id: ResourceId | None = Field(default=None, gt=0)
    step_id: ResourceId | None = Field(default=None, gt=0)
    result_ref: dict = Field(default_factory=dict)
    is_meaningful: bool = True
