"""动作素材公共契约：设计入参、对外视图与冻结评审依据。"""

from datetime import datetime
from typing import Annotated, Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field

from features.media.identity_contracts import CharacterCardSnapshot

ABSOLUTE_MAX_DURATION_SECONDS = 15

ActionClipKind = Literal["once", "loop"]
ActionTrigger = Literal["chat", "manual", "periodic", "user_return"]
ActionCondition = Annotated[str, Field(max_length=120)]
ActionAssetStatus = Literal["pending", "reviewing", "approved", "deferred", "rejected", "failed"]
ActionLibraryGroup = Literal["in_library", "not_in_library"]
ActionAssetSource = Literal["user_requested", "autonomous", "preset"]


class ActionAssetDesign(BaseModel):
    """新建或编辑动作素材的语义定义。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)
    use_when: list[ActionCondition] = Field(default_factory=list, max_length=8)
    avoid_when: list[ActionCondition] = Field(default_factory=list, max_length=8)
    allowed_triggers: list[ActionTrigger] = Field(default_factory=list, max_length=8)
    clip_kind: ActionClipKind = "once"
    duration_seconds: int = Field(default=4, ge=1, le=ABSOLUTE_MAX_DURATION_SECONDS, strict=True)
    reason: str = Field(default="", max_length=400)

    @property
    def triggers(self) -> list[ActionTrigger]:
        return self.allowed_triggers or ["chat", "manual"]


class ActionAssetView(BaseModel):
    """动作素材对外视图；名称不承担跨来源唯一身份。"""

    model_config = ConfigDict(extra="forbid")

    id: ResourceId
    name: str
    motion_description: str
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    allowed_triggers: list[ActionTrigger] = Field(default_factory=list)
    clip_kind: ActionClipKind
    duration_seconds: int
    reason: str = ""
    source: ActionAssetSource
    preset_key: str | None = None
    status: ActionAssetStatus
    review_reason: str = ""
    created_at: datetime
    updated_at: datetime


class ActionAssetUpdate(ActionAssetDesign):
    expected_updated_at: datetime


class ActionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime


class ActionReviewSource(BaseModel):
    """冻结评审依据：角色身体能力与动作含义；环境与道具在组合制作时判断。"""

    model_config = ConfigDict(extra="forbid")

    identity_path: str = Field(min_length=1)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    portrait_path: str = Field(min_length=1)
    portrait_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: CharacterCardSnapshot
    persona: dict[str, str] = Field(default_factory=dict)


class ActionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ActionAssetView]
    total: int | None = None
    has_more: bool
    next_cursor: str | None = None
    limit: int
