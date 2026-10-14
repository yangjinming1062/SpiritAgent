from dataclasses import dataclass
from datetime import date
from typing import Any


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
