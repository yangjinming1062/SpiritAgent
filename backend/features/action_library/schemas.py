"""共享动作语义、冻结来源与独立画面版本的公共契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from features.media.identity_contracts import CharacterCardSnapshot

ActionTargetKind = Literal["window", "desktop"]
ActionSourceScope = Literal["outfit", "target"]
ActionVariantStatus = Literal[
    "not_generated",
    "queued",
    "processing",
    "ready",
    "failed",
    "blocked",
    "excluded",
    "disabled",
]


class ActionDefinitionDesign(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)
    use_when: list[str] = Field(default_factory=list, max_length=8)
    avoid_when: list[str] = Field(default_factory=list, max_length=8)
    clip_kind: Literal["once", "loop"] = "once"
    duration_seconds: int = Field(default=4, ge=1, le=15, strict=True)
    reason: str = Field(default="", max_length=400)

    @field_validator("name", "motion_description")
    @classmethod
    def nonblank(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("动作名称与描述不能为空或包含空字符")
        if info.field_name == "motion_description" and len(value.strip()) < 10:
            raise ValueError("动作描述至少需要10个字符")
        return value.strip()

    @field_validator("use_when", "avoid_when")
    @classmethod
    def bounded_conditions(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 120 for item in value):
            raise ValueError("动作条件须为不超过120字的非空文字")
        return value


class ActionDefinitionSnapshot(ActionDefinitionDesign):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    id: int
    user_id: int
    outfit_id: int | None
    source_visual_key: str
    scope: ActionSourceScope
    origin_kind: ActionTargetKind
    origin_target_id: int
    source: Literal["user_requested", "autonomous"]
    status: str
    review_reason: str = ""
    reuse_definition_id: int | None = None


class ActionSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int
    outfit_id: int | None
    target_kind: ActionTargetKind
    target_id: int
    label: str
    source_visual_key: str
    scope: ActionSourceScope = "outfit"
    ready: bool
    blocked_reason: str | None = None
    reference_paths: list[str] = Field(default_factory=list)
    reference_roles: list[str] = Field(default_factory=list)
    identity: CharacterCardSnapshot | None = None
    outfit_description: str = ""
    persona: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_scope(self) -> "ActionSourceSnapshot":
        if self.scope == "target" and (self.target_kind != "desktop" or self.outfit_id is not None):
            raise ValueError("独立动作来源须为不关联着装的桌景")
        return self


class ActionVariantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_kind: ActionTargetKind
    target_id: int
    action_id: int | None = None
    label: str
    status: ActionVariantStatus
    has_ready_asset: bool = False
    can_generate: bool = False
    reason: str | None = None


class ActionTargetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    id: int
    user_id: int
    definition_id: int
    target_kind: ActionTargetKind
    target_id: int
    action_id: int | None
    automatic: bool
    state: Literal["manual", "pending", "dispatched", "blocked", "excluded"]
    reason: str | None = None


class DefinitionAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_id: int
    status: str
    action_id: int | None = None
    message: str = ""
