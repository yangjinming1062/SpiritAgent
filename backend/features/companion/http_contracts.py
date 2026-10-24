"""陪伴统计与自主咨询的 HTTP 输入和结果。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InteractionStatsRequest(CompanionRequest):
    kind: Literal["chat_turn"]
    hour: int = Field(ge=0, le=23)


class InteractionStatsResult(BaseModel):
    recorded: Literal["chat_turn"]
    threshold_met: bool
    peak_hour: int | None


class IdleExpressionRequest(CompanionRequest):
    trigger: Literal["periodic", "user_return"] = "periodic"
    idle_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    local_hour: int | None = Field(default=None, ge=-1, le=23)


class IdleExpressionResult(BaseModel):
    expressed: bool
    action_id: int | None = None
    reason: str = ""
