"""有明确采纳结论的资源写入契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ResourceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject"]
