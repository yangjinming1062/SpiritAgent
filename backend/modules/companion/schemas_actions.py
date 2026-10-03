"""动作库跨边界 schema：工具入参、REST 响应、播放指令与回执。source / user_id / 预算日 / 系统槽位由服务端绑定。"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

ABSOLUTE_MAX_DURATION_SECONDS = 15.0


class PeekGeometry(BaseModel):
    """成品画布中的遮挡线与覆盖各采样帧的识别区域。"""

    model_config = ConfigDict(extra="forbid")

    side: Literal["left", "right"]
    cut_x: float = Field(gt=0.1, lt=0.9)
    focus_rect: tuple[float, float, float, float]

    @field_validator("focus_rect")
    @classmethod
    def validate_focus_rect(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        left, top, right, bottom = value
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("focus_rect must be an ordered normalized rectangle")
        return value

    @model_validator(mode="after")
    def focus_is_on_revealed_side(self) -> "PeekGeometry":
        left, _top, right, _bottom = self.focus_rect
        if self.side == "left" and right >= self.cut_x:
            raise ValueError("left focus region must be left of the cut")
        if self.side == "right" and left <= self.cut_x:
            raise ValueError("right focus region must be right of the cut")
        return self

    @classmethod
    def from_stored_json(cls, raw: str | None) -> "PeekGeometry | None":
        if not raw:
            return None
        try:
            return cls.model_validate_json(raw)
        except ValidationError:
            return None


def parse_content_rect(raw: str | None) -> tuple[float, float, float, float] | None:
    """解析持久化的内容轮廓；缺失或非法时返回 None。"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list | tuple) or len(data) != 4:
        return None
    try:
        left, top, right, bottom = (float(value) for value in data)
    except (TypeError, ValueError):
        return None
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        return None
    return left, top, right, bottom


class ActionDesignRequest(BaseModel):
    """LLM action_design 工具入参。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    motion_description: str = Field(min_length=10, max_length=600)
    use_when: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=8)
    avoid_when: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=400)
    duration_seconds: float = Field(ge=1, le=ABSOLUTE_MAX_DURATION_SECONDS)
    clip_kind: str = Field(pattern="^(loop|once)$")
    expected_pack_id: int | None = None

    @field_validator("duration_seconds")
    @classmethod
    def validate_integer_duration(cls, value: float) -> float:
        if abs(value - round(value)) > 1e-6:
            raise ValueError("动作时长需为整秒")
        return value


class ActionDesignResult(BaseModel):
    """action_design 受理结果：reused / pending_review / rejected。"""

    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern="^(reused|pending_review|rejected)$")
    proposal_id: int | None = None
    action_id: int | None = None
    message: str = ""


class ActionPlayRequest(BaseModel):
    """LLM action_play 工具入参。"""

    model_config = ConfigDict(extra="forbid")

    action_id: int
    reason: str = Field(default="", max_length=200)
    expected_pack_id: int | None = None


class ActionPlayResult(BaseModel):
    """action_play 结果：queued 不等于 completed。"""

    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern="^(queued|rejected)$")
    play_id: str = ""
    message: str = ""


class ActionPlaybackReceipt(BaseModel):
    """客户端播放回执：按 play_id 幂等聚合。"""

    model_config = ConfigDict(extra="forbid")

    play_id: str
    status: str = Field(pattern="^(started|completed|interrupted|rejected)$")
    visible_duration_ms: int = 0
    error: str | None = None


class ActionPlayCommand(BaseModel):
    """companion.action.play_requested 载荷。"""

    model_config = ConfigDict(extra="forbid")

    play_id: str
    pack_id: int
    appearance_epoch: int
    action_id: int
    asset_revision_id: int | None = None
    repeat_count: int = Field(default=1, ge=1, le=5)
    expires_at: str | None = None
    source: str = "chat_expression"


class ActionCatalogResponse(BaseModel):
    """当前包的目录指针。appearance_epoch 为包的激活代次，客户端据此判断播放指令新旧。"""

    model_config = ConfigDict(extra="forbid")

    pack_id: int
    catalog_version: int
    appearance_epoch: int
    manifest_url: str | None = None


class ActionBudgetStatus(BaseModel):
    """当前用户动态动作制作额度状态（滚动24小时）。
    上限实际值由后台配置给出，schema 不背书默认。"""

    model_config = ConfigDict(extra="forbid")

    budget_date: str
    window_start: str
    window_end: str
    autonomous_create_used: int = 0
    autonomous_create_limit: int = 0
    user_requested_create_used: int = 0
    user_requested_create_limit: int = 0
