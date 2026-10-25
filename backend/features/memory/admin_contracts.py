from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from features.memory.contracts import MemoryListItem
from features.memory.profile_fields import MemoryProfileChange


class MemoryListResponse(BaseModel):
    memories: list[MemoryListItem]
    counts: dict[str, int]
    total: int | None = None
    has_more: bool = False
    next_cursor: str | None = None
    system_preset_id: str


class MemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_preset_id: str
    content: str
    expected_version: int = Field(ge=1)


class MemoryProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_preset_id: Literal["companion"]
    changes: list[MemoryProfileChange] = Field(max_length=5)


class MemoryProfileResponse(BaseModel):
    memories: list[MemoryListItem]
    system_preset_id: Literal["companion"] = "companion"


class MemoryDeleteResponse(BaseModel):
    deleted: bool
