"""Pydantic 契约：伙伴日记 / 片刻 REST 接口。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .journal import DiarySource

MomentMediaTypeLiteral = Literal["", "image", "video", "audio"]


class MomentCommentResponse(BaseModel):
    id: str
    moment_id: str
    role: str
    content: str
    created_at: datetime | None = None


class MomentResponse(BaseModel):
    id: str
    occurred_at: datetime
    # kind / source 为自由字符串：存量行含已停写的枚举值（greeting/user/system 等），仍需可读
    kind: str
    title: str
    body: str
    emotion: str | None = None
    media_url: str | None = None
    media_type: MomentMediaTypeLiteral = ""
    audio_url: str | None = None
    media_metadata: dict | None = None
    source: str
    comments: list[MomentCommentResponse] = Field(default_factory=list)


class MomentListResponse(BaseModel):
    moments: list[MomentResponse]
    next_cursor: str | None = None


class MomentCommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500)


class DiaryEntryResponse(BaseModel):
    id: str
    entry_date: date
    title: str
    body: str
    mood: str | None = None
    source: DiarySource
    memory_ids: list[str] = Field(default_factory=list)
    moment_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DiaryListResponse(BaseModel):
    entries: list[DiaryEntryResponse]
