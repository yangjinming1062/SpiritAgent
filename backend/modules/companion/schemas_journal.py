"""Pydantic 契约：伙伴日记 / 片刻 REST 接口。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MomentKindLiteral = Literal["greeting", "emotion", "together", "milestone", "scene", "user"]
MomentSourceLiteral = Literal["system", "nightly", "llm", "user"]
MomentVisibilityLiteral = Literal["shown", "hidden"]
MomentMediaTypeLiteral = Literal["", "image", "video", "audio"]
DiarySourceLiteral = Literal["nightly", "llm", "user"]


class MomentResponse(BaseModel):
    id: str
    occurred_at: datetime
    kind: MomentKindLiteral
    title: str
    body: str
    emotion: str | None = None
    media_url: str | None = None
    media_type: MomentMediaTypeLiteral = ""
    audio_url: str | None = None
    media_metadata: dict | None = None
    source: MomentSourceLiteral
    visibility: MomentVisibilityLiteral = "shown"


class MomentListResponse(BaseModel):
    moments: list[MomentResponse]
    next_cursor: str | None = None


class MomentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=500)
    emotion: str | None = Field(default=None, max_length=32)
    media_id: str | None = None
    kind: MomentKindLiteral = "user"


class MomentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=64)
    body: str | None = Field(default=None, max_length=500)
    visibility: MomentVisibilityLiteral | None = None


class DiaryEntryResponse(BaseModel):
    id: str
    entry_date: date
    title: str
    body: str
    mood: str | None = None
    source: DiarySourceLiteral
    memory_ids: list[str] = Field(default_factory=list)
    moment_ids: list[str] = Field(default_factory=list)
    edited_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DiaryListResponse(BaseModel):
    entries: list[DiaryEntryResponse]


class DiaryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_date: date | None = None
    title: str | None = Field(default=None, max_length=128)
    body: str = Field(min_length=1, max_length=2000)
    mood: str | None = Field(default=None, max_length=32)


class DiaryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=128)
    body: str | None = Field(default=None, max_length=2000)
    mood: str | None = Field(default=None, max_length=32)
