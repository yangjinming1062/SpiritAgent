from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class DiaryIndexSource:
    user_id: int
    entry_date: date
    title: str
    body: str


@dataclass(frozen=True)
class NarrativeMemorySnapshot:
    id: int
    content: str
    content_version: int
    basis: str
    context: str | None
    source_refs: dict[str, Any]


class MemoryEvidence(BaseModel):
    fingerprint: str
    message_id: int | None = Field(default=None, exclude_if=lambda value: value is None)
    session_id: int | None = Field(default=None, exclude_if=lambda value: value is None)
    quote: str | None = Field(default=None, exclude_if=lambda value: value is None)
    stance: Literal["supports", "opposes"] | None = Field(default=None, exclude_if=lambda value: value is None)
    created_at: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_unavailable: bool | None = Field(default=None, exclude_if=lambda value: value is None)


class MemoryRecord(BaseModel):
    id: int
    content_version: int
    content: str
    context: str | None
    tags: str | None
    basis: Literal["explicit", "inferred", "observed", "system"]
    status: Literal["active", "candidate", "invalidated", "forgotten"]
    usage: Literal["contextual", "background"]
    reason: str
    source_kind: str
    updated_at: str
    evidence: list[MemoryEvidence]
    expires_at: str | None
    reviewed_at: str | None


class MemoryConflictError(ValueError):
    pass


class MemoryListItem(MemoryRecord):
    system_preset_id: str
    importance: float
    created_at: str
