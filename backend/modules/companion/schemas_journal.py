"""日记 REST 契约。"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DIARY_BODY_MAX_CHARS = 2000


class DiaryContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=DIARY_BODY_MAX_CHARS)
    mood: str | None = Field(default=None, max_length=32)


class DiaryEntryResponse(BaseModel):
    id: str
    entry_date: date
    title: str
    body: str
    mood: str | None = None
    post_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DiaryListResponse(BaseModel):
    entries: list[DiaryEntryResponse]
    unread_diary_ids: list[str]


class DiaryUnreadResponse(BaseModel):
    has_unread: bool


class DiaryReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diary_ids: list[UUID] = Field(min_length=1)
