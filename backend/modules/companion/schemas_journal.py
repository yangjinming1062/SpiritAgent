"""日记 REST 契约。"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from .journal import DiarySource


class DiaryEntryResponse(BaseModel):
    id: str
    entry_date: date
    title: str
    body: str
    mood: str | None = None
    source: DiarySource
    post_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DiaryListResponse(BaseModel):
    entries: list[DiaryEntryResponse]
