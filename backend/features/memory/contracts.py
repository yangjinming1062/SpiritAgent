from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, Field
from runtime.memory_scope import MemorySource


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
    context: str
    local_date: str | None


class MemoryEvidence(BaseModel):
    fingerprint: str
    message_id: ResourceId | None = Field(default=None, exclude_if=lambda value: value is None)
    quote: str | None = Field(default=None, exclude_if=lambda value: value is None)
    stance: Literal["supports", "opposes"] | None = Field(default=None, exclude_if=lambda value: value is None)
    created_at: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_unavailable: bool | None = Field(default=None, exclude_if=lambda value: value is None)


class MemorySummary(BaseModel):
    id: ResourceId
    content_version: int
    content: str
    context: str
    kind: str
    slot_key: str | None
    category: str | None
    tags: list[str]
    basis: Literal["explicit", "inferred", "observed", "system"]
    status: Literal["active", "candidate", "invalidated", "forgotten"]
    usage: Literal["contextual", "background"]
    reason: str
    source_kind: str
    updated_at: str
    expires_at: str | None
    reviewed_at: str | None


class MemoryRecord(MemorySummary):
    evidence: list[MemoryEvidence]


class MemoryConflictError(ValueError):
    pass


class MemoryListItem(MemorySummary):
    system_preset_id: str
    importance: float
    created_at: str


class MemoryDetail(MemoryListItem):
    evidence: list[MemoryEvidence]
    revisions: list[dict[str, Any]]


@dataclass(frozen=True)
class SystemMemoryWrite:
    content: str
    context: str
    tags: tuple[str, ...]
    source: MemorySource
    slot_key: str
    source_event_id: int | None = None
