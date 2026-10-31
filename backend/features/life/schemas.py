"""生活状态的公开 DTO 与请求。"""

from datetime import datetime
from typing import Any, Literal

from infrastructure.api_ids import ResourceId
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer

from features.media.visual_semantics import ActivityType, SceneTags

from .actions import LifeAction, NextAction, ReadingResultView
from .prompts import life_content_payload

FactType = Literal["intent", "virtual_activity", "execution", "result"]
PursuitStatus = Literal["active", "paused", "completed", "abandoned"]

UNRESOLVED_STEP_STATES = frozenset({"planned", "running", "unknown"})
TERMINAL_STEP_STATES = frozenset({"completed", "failed", "cancelled", "unknown"})
EVIDENCE_EVENT_KINDS = frozenset({"life.step.completed", "visual.ready", "post.published", "diary.published"})
EVIDENCE_EVENTS_PER_PURSUIT = 24


LIFE_REFERENCE_KEYS = frozenset(
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
        "reading_step_id",
        "cron_job_id",
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
        if key in LIFE_REFERENCE_KEYS and isinstance(item, int) and not isinstance(item, bool):
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

    def prompt_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"event_key", "idempotency_key", "business_ref", "action"})
        # 技术诊断只留在任务与日志边界；生活判断模型只接收稳定的失败类别。
        payload.pop("error", None)
        payload.pop("wait_reason", None)
        if failure_code := payload.pop("failure_code", None):
            payload["failure"] = {"code": failure_code}
        result = payload.pop("result_ref", {})
        payload["has_result"] = bool(result)
        payload["result"] = life_content_payload(result)
        if "fact" in payload:
            payload["fact"] = life_content_payload(payload["fact"])
        return payload


class LifeNarrativePatch(BaseModel):
    """模型生成的伙伴叙事；与执行字段和诊断信息分开保存。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    state: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="生活区的第一人称近况，通常一两句；只说明此刻有依据的活动、打算或感受，不变时为空",
    )
    pursuit: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="事项卡片及详情的第一人称叙事：缘由、真实进展及下一步；调整事项时完整替换，通常两三句；redirect_pursuit 时描述新事项，旧事项以 event 收束",
    )
    action: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        description="准备采取的行动及其目的，第一人称计划，通常一两句；与 next_action 对应，不预写结果",
    )
    event: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="本次生活变化的第一人称记录，通常一两句；仅叙述本轮已决定的变化及已有依据的结果",
    )


class LifeStateView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: ResourceId
    current_activity: str
    narrative: str | None = None
    activity_kind: FactType = "virtual_activity"
    activity_type: ActivityType = "idle"
    scene_tags: SceneTags = Field(default_factory=list)
    activity_started_at: datetime | None = None
    activity_step_id: ResourceId | None = None
    presentation_revision: int = 0
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
    narrative: str | None = None
    reason: str
    desired_result: str
    progress: str
    next_step: str
    status: PursuitStatus
    status_reason: str = ""
    status_source: str = "companion"
    status_changed_at: datetime | None = None
    resume_after: datetime | None = None
    previous_pursuit_id: ResourceId | None = None
    started_at: datetime
    completed_at: datetime | None
    result_ref: dict
    updated_at: datetime


class LifeStatePublicView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: ResourceId
    narrative: str | None
    activity_kind: FactType
    activity_type: ActivityType
    activity_started_at: datetime | None
    active_pursuit_id: ResourceId | None
    revision: int
    updated_at: datetime


class LifePursuitPublicView(LifeResultView):
    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    narrative: str | None
    status: PursuitStatus
    status_source: str
    status_changed_at: datetime | None
    resume_after: datetime | None
    previous_pursuit_id: ResourceId | None
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
    narrative: str | None = None
    state: Literal["planned", "running", "completed", "failed", "cancelled", "unknown"]
    source: str = "life"
    action: LifeAction | None = None
    disposition: str = "ready"
    wait_reason: str = ""
    not_before: datetime | None = None
    reading_result: ReadingResultView | None = None
    business_ref: dict
    result_ref: dict
    error: str | None
    failure_code: str | None = None
    started_at: datetime | None
    finished_at: datetime | None


class LifeStepPublicView(LifeResultView):
    """用户生活接口的步骤投影；不包含执行描述、等待原因或诊断原文。"""

    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    pursuit_id: ResourceId | None
    kind: str
    narrative: str | None = None
    state: Literal["planned", "running", "completed", "failed", "cancelled", "unknown"]
    source: str = "life"
    disposition: str = "ready"
    not_before: datetime | None = None
    reading_result: ReadingResultView | None = None
    result_ref: dict
    failure_code: str | None = None
    started_at: datetime | None
    finished_at: datetime | None


class LifeEventView(LifeResultView):
    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    event_key: str
    kind: str
    source: str
    subject: str
    fact_type: FactType = "execution"
    fact: dict
    narrative: str | None = None
    occurred_at: datetime
    pursuit_id: ResourceId | None
    step_id: ResourceId | None
    result_ref: dict
    is_meaningful: bool


class LifeEventPublicView(LifeResultView):
    """用户生活接口的事件投影；事实细节只进入内部模型上下文。"""

    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    event_key: str
    kind: str
    source: str
    subject: str
    fact_type: FactType = "execution"
    narrative: str | None = None
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
    paused_pursuits: list[LifePursuitView] = Field(default_factory=list)
    recent_events: list[LifeEventView] = Field(default_factory=list)

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "state": self.state.model_dump(
                mode="json",
                exclude={
                    "user_id",
                    "next_review_at",
                    "revision",
                    "scene_revision",
                    "presentation_revision",
                    "updated_at",
                },
            ),
            "active_pursuits": [item.prompt_payload() for item in self.active_pursuits],
            "paused_pursuits": [item.prompt_payload() for item in self.paused_pursuits],
            "recent_events": [item.prompt_payload() for item in self.recent_events],
        }


class LifePursuitListResponse(BaseModel):
    items: list[LifePursuitView] = Field(default_factory=list)
    total: int | None = None
    has_more: bool
    next_cursor: str | None


class LifePursuitPublicListResponse(BaseModel):
    items: list[LifePursuitPublicView] = Field(default_factory=list)
    total: int | None = None
    has_more: bool
    next_cursor: str | None


class LifeEventListResponse(BaseModel):
    items: list[LifeEventView] = Field(default_factory=list)
    total: int | None = None
    has_more: bool
    next_cursor: str | None


class LifeEventPublicListResponse(BaseModel):
    items: list[LifeEventPublicView] = Field(default_factory=list)
    total: int | None = None
    has_more: bool
    next_cursor: str | None


class LifeDecisionFields(BaseModel):
    """模型判断与领域提交共用的生活操作。"""

    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "keep",
        "update",
        "start_pursuit",
        "advance_pursuit",
        "complete_pursuit",
        "pause_pursuit",
        "resume_pursuit",
        "abandon_pursuit",
        "redirect_pursuit",
    ] = "keep"
    activity_kind: Literal["intent", "virtual_activity"] | None = Field(
        default=None, description="意向或虚拟生活情景；真实执行及成果由实际任务记录"
    )
    activity_type: ActivityType | None = Field(
        default=None,
        description=(
            "当前活动的结构化类别（idle/reading/working/creating/thinking/resting/organizing/socializing）；"
            "描述伙伴现在实际进行的活动的性质，计划中的活动不能据此标记为正在进行"
        ),
    )
    scene_tags: SceneTags | None = Field(
        default=None,
        description="当前环境与可用物品的标签：indoor 室内、outdoor 室外、desk 桌面、seat 座位、bed 床、open_space 开阔空间、book 书、computer 电脑、cup 杯子、tools 工具。仅填写有依据的当前条件；变化时提供完整列表，未知或不再具备时用空列表，不变时省略。",
    )
    next_action: NextAction | None = Field(
        default=None, description="登记一个实际可执行的下一步；无须行动时省略，不代表已执行"
    )
    narrative: LifeNarrativePatch = Field(
        default_factory=LifeNarrativePatch,
        description=(
            "面向用户的伙伴第一人称叙事补丁：state 描述当前状态，pursuit 描述持续事项，"
            "action 描述下一步计划，event 描述本次值得记录的变化；只填写本次发生变化的部分。"
        ),
    )
    status_reason: str = Field(default="", max_length=2000, description="暂停、恢复、放弃或改变方向的具体原因")
    resume_after: AwareDatetime | None = Field(
        default=None, description="伙伴自行暂停后最早重新判断的时间，须包含时区；无确定时间时为空"
    )
    current_activity: str | None = Field(
        default=None, max_length=2000, description="伙伴此刻正在做或打算做的具体生活活动；活动本身不变时省略或为 null"
    )
    current_scene: dict | None = Field(
        default=None,
        description="伙伴所处的生活环境与情境，如地点、周围事物和当前状况；变化时提供完整信息并保留未变内容，不变时省略或为 null。事项状态、进展和依据由对应事项字段表达",
    )
    mood: str | None = Field(default=None, max_length=200, description="伙伴当前心情；不变时省略或为 null")
    pursuit_id: ResourceId | None = Field(default=None, gt=0, description="本次推进、结束或调整的已有事情的 id")
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
    source: Literal["companion", "user"] = "companion"
    request_id: str | None = Field(default=None, max_length=180)


class LifeStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=32)
    description: str
    narrative: str | None = Field(default=None, max_length=1000)
    pursuit_id: ResourceId | None = Field(default=None, gt=0)


class LifePursuitEvidence(BaseModel):
    steps: list[LifeStepView] = Field(default_factory=list)
    events: list[LifeEventView] = Field(default_factory=list)

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "steps": [item.prompt_payload() for item in self.steps],
            "events": [item.prompt_payload() for item in self.events],
        }


class LifeEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1, max_length=180)
    kind: str = Field(min_length=1, max_length=48)
    source: str = Field(min_length=1, max_length=48)
    subject: Literal["companion", "user", "system"] = "companion"
    fact_type: FactType = "execution"
    fact: dict = Field(default_factory=dict)
    narrative: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None
    pursuit_id: ResourceId | None = Field(default=None, gt=0)
    step_id: ResourceId | None = Field(default=None, gt=0)
    result_ref: dict = Field(default_factory=dict)
    is_meaningful: bool = True


class LifePursuitDetail(LifePursuitView):
    steps: list[LifeStepView] = Field(default_factory=list)


class LifePursuitPublicDetail(LifePursuitPublicView):
    steps: list[LifeStepPublicView] = Field(default_factory=list)
